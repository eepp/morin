# SPDX-FileCopyrightText: 2026 Philippe Proulx <eeppeliteloop@gmail.com>
# SPDX-License-Identifier: MIT

import asyncio


class ChromiumError(Exception):
    pass


# Tasks waiting for the `chromium` processes to exit, kept here so that
# they're not garbage collected while running.
_wait_tasks: set[asyncio.Task[int]] = set()


# Opens `url` in a new tab of the current Chromium window.
#
# A running Chromium instance gets `url` from the new `chromium`
# process, which then exits. Without one, the new process becomes the
# browser itself: don't wait for it, but still reap it when it exits.
#
# Raises `ChromiumError` on failure.
async def open_url(url: str) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            'chromium',
            url,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise ChromiumError(f"Can't run `chromium`: {exc}") from exc

    task = asyncio.create_task(proc.wait())
    _wait_tasks.add(task)
    task.add_done_callback(_wait_tasks.discard)
