# SPDX-FileCopyrightText: 2026 Philippe Proulx <eeppeliteloop@gmail.com>
# SPDX-License-Identifier: MIT

import asyncio
import contextlib
import random
import signal
import sys
from collections.abc import Callable

from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Devices.StreamDeck import StreamDeck

from morin import chromium, kitty, visual
from morin.cfg import Cfg, CfgError, Link, load_cfg
from morin.herdr import AgentLoc, HerdrClient, HerdrError, HerdrState


# Shows the agents of a Herdr server on the keys of a Stream Deck, from
# left to right in the Herdr order, and the configured links on the
# bottom row, from right to left.
#
# Pressing an agent key brings the kitty window running the Herdr client
# to the front, and then focuses the agent pane of the key.
#
# Pressing a link key opens its URL with Chromium.
# Flips the blinking phase of an agent key with a random period from
# 250 ms to 400 ms, calling `on_flip` after each flip, until cancelled.
#
# Different periods make the blinking keys look less mechanical.
class _Blinker:
    def __init__(self, on_flip: Callable[[], None]) -> None:
        self._period = random.uniform(0.25, 0.4)
        self._alt = False
        self._task = asyncio.create_task(self._run(on_flip))

    # Whether the key is in its alternate phase.
    @property
    def alt(self) -> bool:
        return self._alt

    def cancel(self) -> None:
        self._task.cancel()

    async def _run(self, on_flip: Callable[[], None]) -> None:
        while True:
            await asyncio.sleep(self._period)
            self._alt = not self._alt
            on_flip()


class _App:
    def __init__(self, deck: StreamDeck, client: HerdrClient, cfg: Cfg) -> None:
        self._deck = deck
        self._client = client
        self._cfg = cfg
        self._key_locs: list[AgentLoc] = []
        rows, cols = deck.key_layout()

        # Link of each key of the bottom row, from the last key
        self._key_links = {
            deck.key_count() - 1 - index: link for index, link in enumerate(cfg.links[:cols])
        }

        # Agents get the first keys: all the keys but the bottom row by
        # default, but never a link key
        max_agent_keys = cfg.max_agent_keys or (rows - 1) * cols
        self._agent_key_count = min(max_agent_keys, deck.key_count() - len(self._key_links))

        self._key_images: list[bytes | None] = [None] * deck.key_count()
        self._state: HerdrState | None = None

        # Blinker of each existing agent, by pane ID
        self._blinkers: dict[str, _Blinker] = {}

    async def run(self) -> None:
        self._deck.set_key_callback_async(self._on_key, asyncio.get_running_loop())

        try:
            async with self._client.monitor() as monitor:
                async for state in monitor:
                    self._state = state
                    self._sync_blinkers()
                    self._show()
        finally:
            for blinker in self._blinkers.values():
                blinker.cancel()

            self._deck.set_key_callback(None)

    # Gives a blinker to each new agent and drops the blinkers of the
    # agents which don't exist anymore.
    def _sync_blinkers(self) -> None:
        assert self._state is not None
        pane_ids = {agent.loc.pane_id for agent in self._state.agents}

        for pane_id in self._blinkers.keys() - pane_ids:
            self._blinkers.pop(pane_id).cancel()

        for pane_id in pane_ids - self._blinkers.keys():
            self._blinkers[pane_id] = _Blinker(self._show)

    def _show(self) -> None:
        agents = [] if self._state is None else self._state.agents[: self._agent_key_count]
        self._key_locs = [agent.loc for agent in agents]

        for key in range(self._deck.key_count()):
            if key < len(agents):
                agent = agents[key]
                image = visual.agent_key_image(
                    self._deck, agent, self._cfg, self._blinkers[agent.loc.pane_id].alt
                )
            elif (link := self._key_links.get(key)) is not None:
                image = visual.picture_key_image(self._deck, link.image)
            else:
                image = visual.blank_key_image(self._deck)

            # Only send what changed to the device
            if image != self._key_images[key]:
                self._deck.set_key_image(key, image)
                self._key_images[key] = image

    async def _on_key(self, _deck: StreamDeck, key: int, pressed: bool) -> None:
        if not pressed:
            return

        if key < len(self._key_locs):
            await asyncio.gather(
                self._focus_herdr_window(), self._focus_herdr_agent_pane(self._key_locs[key])
            )
        elif (link := self._key_links.get(key)) is not None:
            await self._open_link(link)

    async def _focus_herdr_window(self) -> None:
        try:
            await kitty.focus_window_running('herdr')
        except kitty.KittyError as exc:
            _warn(f"Can't focus the Herdr terminal window: {exc}")

    async def _focus_herdr_agent_pane(self, loc: AgentLoc) -> None:
        try:
            await self._client.focus_agent_pane(loc)
        except HerdrError as exc:
            _warn(f"Can't focus agent pane: {exc}")

    async def _open_link(self, link: Link) -> None:
        try:
            await chromium.open_url(link.url)
        except chromium.ChromiumError as exc:
            _warn(f"Can't open `{link.url}`: {exc}")


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# Runs the app until it's cancelled, for example by `SIGTERM` when
# systemd stops the service.
async def _run(deck: StreamDeck, cfg: Cfg) -> None:
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)

    with contextlib.suppress(asyncio.CancelledError):
        await _App(deck, HerdrClient(), cfg).run()


def main() -> None:
    try:
        cfg = load_cfg()
    except CfgError as exc:
        raise SystemExit(str(exc)) from None

    decks = DeviceManager().enumerate()

    if not decks:
        raise SystemExit('No Stream Deck found')

    deck = decks[0]
    deck.open()

    try:
        deck.reset()
        deck.set_brightness(100)

        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_run(deck, cfg))
    finally:
        deck.reset()
        deck.close()
