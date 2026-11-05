# SPDX-FileCopyrightText: 2026 Philippe Proulx <eeppeliteloop@gmail.com>
# SPDX-License-Identifier: MIT

import asyncio
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

type _Json = dict[str, Any]


class KittyError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _Window:
    # Remote control socket of the kitty instance.
    sock_path: Path

    id: int

    # X window ID of the containing OS window, if known.
    os_window_x_id: int | None


# Runs the command `args` and returns its standard output.
async def _run(*args: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as exc:
        raise KittyError(f"Can't run `{args[0]}`: {exc}") from exc

    try:
        async with asyncio.timeout(2.0):
            stdout, stderr = await proc.communicate()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise KittyError(f'`{shlex.join(args)}` timed out') from None

    if proc.returncode != 0:
        raise KittyError(stderr.decode().strip() or f'`{shlex.join(args)}` failed')

    return stdout.decode()


# Runs `kitten @ ARGS` against the kitty instance of `sock_path` and
# returns its standard output.
async def _run_kitten(sock_path: Path, *args: str) -> str:
    return await _run('kitten', '@', '--to', f'unix:{sock_path}', *args)


# Returns the remote control socket paths of the running kitty
# instances, as created with `listen_on unix:${XDG_RUNTIME_DIR}/kitty`.
def _sock_paths() -> list[Path]:
    runtime_dir = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}')
    return sorted(path for path in runtime_dir.glob('kitty-*') if path.is_socket())


def _runs(json_window: _Json, program: str) -> bool:
    return any(
        (cmdline := proc.get('cmdline')) and Path(cmdline[0]).name == program
        for proc in json_window.get('foreground_processes', [])
    )


# Returns the first window, among all the running kitty instances, of
# which a foreground process is `program`, or `None` if there's none.
async def _find_window(program: str) -> _Window | None:
    for sock_path in _sock_paths():
        try:
            json_os_windows = json.loads(await _run_kitten(sock_path, 'ls'))
        except (KittyError, json.JSONDecodeError):
            # For example, the stale socket of a dead instance
            continue

        for json_os_window in json_os_windows:
            for json_tab in json_os_window.get('tabs', []):
                for json_window in json_tab.get('windows', []):
                    if _runs(json_window, program):
                        return _Window(
                            sock_path, json_window['id'], json_os_window.get('platform_window_id')
                        )

    return None


# Brings the first kitty window, among all the running kitty instances,
# of which a foreground process is `program` to the front: activates its
# X window, and then makes it the active window of its kitty instance.
#
# Raises `KittyError` on failure.
async def focus_window_running(program: str) -> None:
    window = await _find_window(program)

    if window is None:
        raise KittyError(f'No kitty window running `{program}`')

    if window.os_window_x_id is not None:
        await _run('xdotool', 'windowactivate', str(window.os_window_x_id))

    await _run_kitten(window.sock_path, 'focus-window', '--match', f'id:{window.id}')
