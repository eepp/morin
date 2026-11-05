# SPDX-FileCopyrightText: 2026 Philippe Proulx <eeppeliteloop@gmail.com>
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import contextlib
import enum
import itertools
import json
import os
from collections.abc import AsyncGenerator, AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

type Json = dict[str, Any]


# Base class of all Herdr errors.
class HerdrError(Exception):
    pass


# Can't reach the Herdr server or it closed the connection.
class HerdrUnavailError(HerdrError):
    pass


# The Herdr server rejected a request.
class HerdrRequestError(HerdrError):
    def __init__(self, code: str, msg: str) -> None:
        super().__init__(f'{msg} ({code})')
        self._code = code
        self._msg = msg

    @property
    def code(self) -> str:
        return self._code

    @property
    def message(self) -> str:
        return self._msg


class AgentStatus(enum.StrEnum):
    IDLE = 'idle'
    WORKING = 'working'
    BLOCKED = 'blocked'
    DONE = 'done'
    UNKNOWN = 'unknown'


# Where an agent lives within the Herdr server.
@dataclass(frozen=True, slots=True)
class AgentLoc:
    workspace_id: str
    tab_id: str
    pane_id: str


@dataclass(frozen=True, slots=True)
class Agent:
    loc: AgentLoc

    # Project (repository) name, for example `babeltrace`.
    project: str

    # Short name (like the agent or tab label).
    name: str

    # Status.
    status: AgentStatus

    # Agent program, for example `claude`.
    kind: str | None

    # Terminal title which the agent sets (often its current task).
    title: str | None

    # Current working directory.
    cwd: str | None


# State of a Herdr server at some point.
@dataclass(frozen=True, slots=True)
class HerdrState:
    is_connected: bool

    # Ordered like in Herdr: by workspace, then by tab.
    agents: tuple[Agent, ...] = ()

    # Returns the agent at `loc` if it still exists.
    def find(self, loc: AgentLoc) -> Agent | None:
        return next((a for a in self.agents if a.loc == loc), None)


# Returns the socket path which the `herdr` CLI would use.
def def_sock_path() -> Path:
    if path := os.environ.get('HERDR_SOCKET_PATH'):
        return Path(path)

    base = Path.home() / '.config/herdr'

    if session := os.environ.get('HERDR_SESSION'):
        return base / 'sessions' / session / 'herdr.sock'

    return base / 'herdr.sock'


def _unwrap(msg: Json) -> Json:
    if error := msg.get('error'):
        raise HerdrRequestError(error.get('code', '?'), error.get('message', '?'))

    return msg.get('result', {})


# Newline-delimited JSON stream to the Herdr server.
class _Conn:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._ids = itertools.count(1)

    async def request(self, method: str, params: Json) -> Json:
        req_id = f'morin-{next(self._ids)}'
        line = json.dumps({'id': req_id, 'method': method, 'params': params})

        try:
            self._writer.write(line.encode() + b'\n')
            await self._writer.drain()
        except OSError as exc:
            raise HerdrUnavailError(f"Can't send to Herdr: {exc}") from exc

        while True:
            msg = await self._recv()

            if msg.get('id') == req_id:
                return _unwrap(msg)

    # Yields each event message: `event` (name) and `data` keys.
    async def events(self) -> AsyncIterator[Json]:
        while True:
            msg = await self._recv()

            # For example, `events_lost` when we're too slow.
            _unwrap(msg)

            if 'event' in msg:
                yield msg

    async def _recv(self) -> Json:
        try:
            line = await self._reader.readline()
        except (OSError, ValueError) as exc:
            raise HerdrUnavailError(f"Can't read from Herdr: {exc}") from exc

        if not line:
            raise HerdrUnavailError('Herdr closed the connection')

        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise HerdrUnavailError(f'Herdr sent invalid JSON: {exc}') from exc


def _agent_name(json_info: Json, json_ws: Json, json_tab: Json) -> str:
    # Explicit agent (pane) label.
    if label := json_info.get('label'):
        return label

    # Renamed tab: an unnamed tab has its number as its label
    tab_label = json_tab.get('label')

    if tab_label and tab_label != str(json_tab.get('number')):
        return tab_label

    # Linked worktree workspace named after its branch
    if (json_ws.get('worktree') or {}).get('is_linked_worktree') and json_ws.get('label'):
        return json_ws['label']

    return json_info.get('terminal_title_stripped') or tab_label or json_info['pane_id']


def _project_name(json_ws: Json, loc: AgentLoc) -> str:
    # A worktree workspace is named after its branch: prefer the repository
    if repo_name := (json_ws.get('worktree') or {}).get('repo_name'):
        return repo_name

    return json_ws.get('label') or loc.workspace_id


def _agents_from_json(json_agents: Json) -> tuple[Agent, ...]:
    json_wss = {ws['workspace_id']: ws for ws in json_agents.get('workspaces', [])}
    json_tabs = {tab['tab_id']: tab for tab in json_agents.get('tabs', [])}
    agents: list[tuple[tuple[int, int, str], Agent]] = []

    for info in json_agents.get('agents', []):
        json_ws = json_wss.get(info['workspace_id'], {})
        json_tab = json_tabs.get(info['tab_id'], {})
        loc = AgentLoc(info['workspace_id'], info['tab_id'], info['pane_id'])

        agent = Agent(
            loc=loc,
            project=_project_name(json_ws, loc),
            name=_agent_name(info, json_ws, json_tab),
            status=AgentStatus(info.get('agent_status', AgentStatus.UNKNOWN)),
            kind=info.get('display_agent') or info.get('agent'),
            title=info.get('terminal_title_stripped') or None,
            cwd=info.get('foreground_cwd') or info.get('cwd'),
        )

        order = (json_ws.get('number', 0), json_tab.get('number', 0), loc.pane_id)
        agents.append((order, agent))

    return tuple(agent for _, agent in sorted(agents, key=lambda item: item[0]))


# Client of a Herdr server.
#
# Each request uses its own short-lived connection, so that a client may
# serve concurrent tasks.
class HerdrClient:
    # Large enough for a session snapshot with many panes.
    _LINE_LIMIT = 64 * 1024 * 1024

    def __init__(self, sock_path: Path | str | None = None) -> None:
        self._sock_path = Path(sock_path) if sock_path else def_sock_path()

    @property
    def sock_path(self) -> Path:
        return self._sock_path

    # Sends a raw socket API request and returns its result.
    async def _request(self, method: str, params: Json | None = None) -> Json:
        async with self._connect() as conn:
            return await conn.request(method, params or {})

    # Returns the current state of the server.
    async def state(self) -> HerdrState:
        return HerdrState(
            is_connected=True,
            agents=_agents_from_json((await self._request('session.snapshot'))['snapshot']),
        )

    # Focuses `agent`, switching workspace and tab as needed.
    async def focus_agent_pane(self, agent: Agent | AgentLoc) -> None:
        loc = agent.loc if isinstance(agent, Agent) else agent
        await self._request('agent.focus', {'target': loc.pane_id})

    # Subscribes to events and yields their async iterator.
    #
    # The subscription is active once the context is entered, and the
    # iterator raises `HerdrError` when it ends.
    @contextlib.asynccontextmanager
    async def _subscribe(
        self, subscriptions: Iterable[Mapping[str, Any]]
    ) -> AsyncGenerator[AsyncIterator[Json]]:
        async with self._connect() as conn:
            await conn.request('events.subscribe', {'subscriptions': list(subscriptions)})

            yield conn.events()

    # Returns a new agent monitor using this client.
    def monitor(self, **kwargs: float) -> AgentMonitor:
        return AgentMonitor(self, **kwargs)

    @contextlib.asynccontextmanager
    async def _connect(self) -> AsyncGenerator[_Conn]:
        try:
            reader, writer = await asyncio.open_unix_connection(
                self._sock_path, limit=self._LINE_LIMIT
            )
        except OSError as exc:
            raise HerdrUnavailError(
                f"Can't connect to Herdr at `{self._sock_path}`: {exc}"
            ) from exc

        try:
            yield _Conn(reader, writer)
        finally:
            writer.close()

            with contextlib.suppress(OSError):
                await writer.wait_closed()


# Live view of the agents of a Herdr server.
#
# Use it as an async context manager, then iterate it to get a new
# `HerdrState` each time it changes, for example:
#
#     async with client.monitor() as monitor:
#         async for state in monitor:
#             ...
#
# The monitor survives server restarts: it reports a disconnected state
# and reconnects by itself.
#
# The monitor treats events only as "something changed" signals and
# always rebuilds its state from a fresh `session.snapshot`.
class AgentMonitor:
    # Events that may change which agents exist or how they're named.
    _STRUCTURE_EVENTS = (
        'pane.created',
        'pane.closed',
        'pane.moved',
        'pane.exited',
        'pane.agent_detected',
        'pane.updated',
        'tab.created',
        'tab.closed',
        'tab.renamed',
        'tab.moved',
        'workspace.created',
        'workspace.closed',
        'workspace.renamed',
        'workspace.updated',
        'workspace.moved',
        'workspace.reordered',
        'workspace.metadata_updated',
    )

    def __init__(
        self,
        client: HerdrClient,
        *,
        retry_delay: float = 2.0,
        resync_interval: float = 30.0,
        debounce: float = 0.05,
    ) -> None:
        self._client = client
        self._retry_delay = retry_delay

        # Safety net in case we ever miss an event
        self._resync_interval = resync_interval

        # Coalesces event bursts into a single snapshot
        self._debounce = debounce

        self._state: HerdrState | None = None
        self._changed = asyncio.Event()
        self._resync = asyncio.Event()
        self._main: asyncio.Task[None] | None = None
        self._task_group: asyncio.TaskGroup | None = None

        # Agent status subscription tasks by pane ID
        self._watchers: dict[str, asyncio.Task[None]] = {}

    # Latest state or `None` before the first sync attempt.
    @property
    def state(self) -> HerdrState | None:
        return self._state

    async def __aenter__(self) -> Self:
        self._main = asyncio.create_task(self._run(), name='herdr-monitor')
        self._main.add_done_callback(lambda _: self._changed.set())
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        assert self._main is not None
        self._main.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await self._main

    def __aiter__(self) -> AsyncIterator[HerdrState]:
        return self.changes()

    # Yields the current state, then each new one.
    async def changes(self) -> AsyncIterator[HerdrState]:
        if self._main is None:
            raise RuntimeError('Enter the monitor with `async with` first')

        last = None

        while True:
            while self._state is None or self._state == last:
                if self._main.done():
                    # Re-raises the failure if any
                    self._main.result()
                    return

                await self._changed.wait()

            last = self._state
            yield last

    def _publish(self, state: HerdrState) -> None:
        if state == self._state:
            return

        self._state = state

        # Wake up current waiters; later ones wait on a new event
        self._changed.set()
        self._changed = asyncio.Event()

    def _request_resync(self) -> None:
        self._resync.set()

    async def _run(self) -> None:
        try:
            async with asyncio.TaskGroup() as self._task_group:
                self._task_group.create_task(self._sync_loop())
                self._task_group.create_task(
                    self._follow([{'type': name} for name in self._STRUCTURE_EVENTS])
                )
        finally:
            self._task_group = None
            self._watchers.clear()

    async def _sync_loop(self) -> None:
        while True:
            # Clear first so that events during the snapshot aren't lost
            self._resync.clear()

            try:
                state = await self._client.state()
            except HerdrError:
                state = HerdrState(is_connected=False)

            self._publish(state)
            self._reconcile_watchers(state.agents)

            delay = self._resync_interval if state.is_connected else self._retry_delay

            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(delay):
                    await self._resync.wait()

            await asyncio.sleep(self._debounce)

    async def _follow(self, subs: list[Json]) -> None:
        # Requests a resync on each event, resubscribing forever
        while True:
            try:
                async with self._client._subscribe(subs) as events:
                    # Catch up on what happened before subscribing
                    self._request_resync()

                    async for _ in events:
                        self._request_resync()
            except HerdrError:
                pass

            self._request_resync()
            await asyncio.sleep(self._retry_delay)

    def _reconcile_watchers(self, agents: Iterable[Agent]) -> None:
        # Herdr only offers agent status events per pane
        assert self._task_group is not None
        pane_ids = {agent.loc.pane_id for agent in agents}

        for pane_id in self._watchers.keys() - pane_ids:
            self._watchers.pop(pane_id).cancel()

        for pane_id in pane_ids - self._watchers.keys():
            self._watchers[pane_id] = self._task_group.create_task(
                self._follow([{'type': 'pane.agent_status_changed', 'pane_id': pane_id}]),
                name=f'herdr-status-{pane_id}',
            )
