# SPDX-FileCopyrightText: 2026 Philippe Proulx <eeppeliteloop@gmail.com>
# SPDX-License-Identifier: MIT

import asyncio
import contextlib
import signal
import sys

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

        # Current phase of the blinking keys
        self._blink_alt = False

    async def run(self) -> None:
        self._deck.set_key_callback_async(self._on_key, asyncio.get_running_loop())
        blink_task = asyncio.create_task(self._blink())

        try:
            async with self._client.monitor() as monitor:
                async for state in monitor:
                    self._state = state
                    self._show()
        finally:
            blink_task.cancel()
            self._deck.set_key_callback(None)

    # Flips the phase of the blinking keys every 300 ms.
    async def _blink(self) -> None:
        while True:
            await asyncio.sleep(0.3)
            self._blink_alt = not self._blink_alt
            self._show()

    def _show(self) -> None:
        agents = [] if self._state is None else self._state.agents[: self._agent_key_count]
        self._key_locs = [agent.loc for agent in agents]

        for key in range(self._deck.key_count()):
            if key < len(agents):
                image = visual.agent_key_image(self._deck, agents[key], self._cfg, self._blink_alt)
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
