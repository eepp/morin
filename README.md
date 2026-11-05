# morin

Shows the status of your [Herdr](https://herdr.dev/) agents on the keys
of a [Stream Deck](https://www.elgato.com/ca/en/p/stream-deck).

Each key is an agent, from left to right in the Herdr order: the project
name in big letters and the agent name below it. Its colour tells the
status of the agent:

| Colour            | Status  |
|-------------------|---------|
| Black             | Idle    |
| Blinking yellow   | Working |
| Green             | Done    |
| Red               | Blocked |
| Grey              | Unknown |

Press a key to bring the kitty window running Herdr to the front and
focus the pane of that agent.

## Setup

This project assumes my super specific setup:

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- A Stream Deck which your user can access
- A running Herdr server
- [kitty](https://sw.kovidgoyal.net/kitty/) with remote control on
- `xdotool` (X11)
- The
  [Roboto Condensed](https://fonts.google.com/specimen/Roboto+Condensed)
  font (optional but it looks better)

It also uses custom labels for my specific project names (for example
"Babeltrace" becomes "BT").

## Run

```
uv sync
uv run morin
```
