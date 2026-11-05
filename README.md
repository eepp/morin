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

By default, agents only use the top rows: the bottom row holds links,
from right to left, which open in a new tab of your current Chromium
window when you press them.

## Setup

This project assumes my super specific setup:

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- A Stream Deck which your user can access
- A running Herdr server
- [kitty](https://sw.kovidgoyal.net/kitty/) with remote control on
- `xdotool` (X11)
- Chromium (`chromium` command)
- The
  [Roboto Condensed](https://fonts.google.com/specimen/Roboto+Condensed)
  font (optional but it looks better)

## Configuration

morin reads the optional `~/.config/morin/config.toml` file (or
`$XDG_CONFIG_HOME/morin/config.toml`):

```toml
# Maximum number of agent keys (default: all the keys but the
# bottom row).
#
# Agent keys never cover link keys, therefore a higher value lets agents
# use the free keys of the bottom row.
max-agent-keys = 10

# Links of the bottom row of keys, from right to left.
#
# `image` is a file name within `~/.config/morin/images`.
[[links]]
url = 'https://review.lttng.org/q/status:open+attention:self'
image = 'paul.jpg'

[[links]]
url = 'https://support.efficios.com/projects/lttng-tools/issues'
image = 'jgalar.jpg'

# Short project names which fit a key.
[project-aliases]
babeltrace = 'BT'
lttng-tools = 'tools'
yactfr = 'yac'

# Key colors of each agent status (`idle`, `working`, `done`, `blocked`,
# or `unknown`): background (`bg`), agent name (`name`) and, to make the
# key blink, alternate background (`alt-bg`).
#
# A missing status keeps its default colors.
[agent-status-colors]
working = { bg = '#fde047', name = '#000000', alt-bg = '#d9a90a' }
done = { bg = '#4ade80', name = '#000000' }
```

A color is anything [Pillow](https://python-pillow.org/) understands,
for example `#fde047` or `black`.

Without a configuration file, there are no links and no project
aliases, with the default colors of the table above.

## Run

```
uv sync
uv run morin
```
