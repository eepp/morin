# SPDX-FileCopyrightText: 2026 Philippe Proulx <eeppeliteloop@gmail.com>
# SPDX-License-Identifier: MIT

import asyncio
import contextlib
import signal
import sys

from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Devices.StreamDeck import StreamDeck

from morin import kitty, visual
from morin.herdr import AgentLoc, HerdrClient, HerdrError, HerdrState


# Shows the agents of a Herdr server on the keys of a Stream Deck, from
# left to right in the Herdr order.
#
# Pressing a key brings the kitty window running the Herdr client to the
# front, and then focuses the agent pane of the key.
class _App:
    def __init__(self, deck: StreamDeck, client: HerdrClient) -> None:
        self._deck = deck
        self._client = client
        self._key_locs: list[AgentLoc] = []
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
        if self._state is None:
            return

        agents = self._state.agents[: self._deck.key_count()]
        self._key_locs = [agent.loc for agent in agents]

        for key in range(self._deck.key_count()):
            if key < len(agents):
                image = visual.agent_key_image(self._deck, agents[key], self._blink_alt)
            else:
                image = visual.blank_key_image(self._deck)

            # Only send what changed to the device
            if image != self._key_images[key]:
                self._deck.set_key_image(key, image)
                self._key_images[key] = image

    async def _on_key(self, _deck: StreamDeck, key: int, pressed: bool) -> None:
        if not pressed or key >= len(self._key_locs):
            return

        await asyncio.gather(
            self._focus_herdr_window(), self._focus_herdr_agent_pane(self._key_locs[key])
        )

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


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# Runs the app until it's cancelled, for example by `SIGTERM` when
# systemd stops the service.
async def _run(deck: StreamDeck) -> None:
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)

    with contextlib.suppress(asyncio.CancelledError):
        await _App(deck, HerdrClient()).run()


def main() -> None:
    decks = DeviceManager().enumerate()

    if not decks:
        raise SystemExit('No Stream Deck found')

    deck = decks[0]
    deck.open()

    try:
        deck.reset()
        deck.set_brightness(100)

        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_run(deck))
    finally:
        deck.reset()
        deck.close()
