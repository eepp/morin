# SPDX-FileCopyrightText: 2026 Philippe Proulx <eeppeliteloop@gmail.com>
# SPDX-License-Identifier: MIT

import functools
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont
from StreamDeck.Devices.StreamDeck import StreamDeck
from StreamDeck.ImageHelpers import PILHelper

from morin.herdr import Agent, AgentStatus

type _Font = ImageFont.FreeTypeFont | ImageFont.ImageFont

# Short project names which fit a key.
_PROJECT_ALIASES = {
    'babeltrace': 'BT',
    'lttng-tools': 'tools',
    'lttng-ust': 'UST',
    'lttng-modules': 'mod',
    'jacquesctf': 'jctf',
    'yactfr': 'yac',
    'barectf': 'bare',
    'lttng-www': 'l-www',
    'normand': 'norm',
    'lttng-ci': 'CI',
}


# Key colors of an agent status.
@dataclass(frozen=True, slots=True)
class _StatusColors:
    bg: str

    # Agent name, readable over `bg`
    name: str

    # Background of the alternate phase of a blinking key, if any
    alt_bg: str | None = None


_STATUS_COLORS: dict[AgentStatus, _StatusColors] = {
    AgentStatus.IDLE: _StatusColors(bg='#000000', name='#ffffff'),
    AgentStatus.DONE: _StatusColors(bg='#4ade80', name='#000000'),
    AgentStatus.WORKING: _StatusColors(bg='#fde047', name='#000000', alt_bg='#f2c00f'),
    AgentStatus.BLOCKED: _StatusColors(bg='#ff5a5a', name='#ffffff'),
    AgentStatus.UNKNOWN: _StatusColors(bg='#475569', name='#ffffff'),
}

# Font files of the project and agent name, which Pillow finds in the
# system font directories.
_PROJECT_FONT = 'RobotoCondensed-Bold.ttf'
_NAME_FONT = 'RobotoCondensed-Medium.ttf'

# Horizontal margin, as a fraction of the key width.
_MARGIN = 0.06


# Returns the font `file_name` with the pixel size `size`, falling back
# to the Pillow default font.
@functools.cache
def _font(file_name: str, size: int) -> _Font:
    try:
        return ImageFont.truetype(file_name, size)
    except OSError:
        return ImageFont.load_default(size)


# Returns `text`, possibly truncated with an ellipsis, and the largest
# font, from `max_size` down to `min_size`, with which it fits
# within `max_width`.
def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_file_name: str,
    max_size: int,
    min_size: int,
    max_width: float,
    stroke_width: int = 0,
) -> tuple[str, _Font]:
    def width(text: str, font: _Font) -> float:
        return draw.textlength(text, font=font) + 2 * stroke_width

    for size in range(max_size, min_size - 1, -1):
        font = _font(font_file_name, size)

        if width(text, font) <= max_width:
            return text, font

    # Still too wide with the smallest font: truncate
    font = _font(font_file_name, min_size)

    while len(text) > 1 and width(f'{text}…', font) > max_width:
        text = text[:-1]

    return f'{text.rstrip()}…', font


def _project_label(project_name: str) -> str:
    return _PROJECT_ALIASES.get(project_name, project_name)


# Only takes what affects the rendering, so that agents sharing it share
# the cached image.
@functools.lru_cache(maxsize=256)
def _render_agent_key_image(
    deck: StreamDeck, status: AgentStatus, alt: bool, project_label: str, name: str
) -> bytes:
    colors = _STATUS_COLORS[status]
    bg = colors.alt_bg if alt and colors.alt_bg is not None else colors.bg
    image: Image.Image = PILHelper.create_key_image(deck, bg)
    draw = ImageDraw.Draw(image)
    width, height = image.size
    max_text_width = width * (1 - 2 * _MARGIN)
    center_x = width / 2

    # Project label: big, white with a black outline
    stroke_width = max(2, round(height * 0.035))

    project_label, project_font = _fit_text(
        draw,
        project_label,
        _PROJECT_FONT,
        max_size=round(height * 0.42),
        min_size=round(height * 0.24),
        max_width=max_text_width,
        stroke_width=stroke_width,
    )

    draw.text(
        (center_x, height * 0.40),
        project_label,
        font=project_font,
        anchor='mm',
        fill='#ffffff',
        stroke_width=stroke_width,
        stroke_fill='#000000',
    )

    # Agent name: smaller, below
    name, name_font = _fit_text(
        draw,
        name,
        _NAME_FONT,
        max_size=round(height * 0.22),
        min_size=round(height * 0.18),
        max_width=max_text_width,
    )

    draw.text(
        (center_x, height * 0.76),
        name,
        font=name_font,
        anchor='mm',
        fill=colors.name,
    )

    return PILHelper.to_native_key_format(deck, image)


# Returns the native key image of `agent` for `deck`, ready
# for StreamDeck.set_key_image().
#
# `alt` selects the alternate phase of a blinking key.
def agent_key_image(deck: StreamDeck, agent: Agent, alt: bool = False) -> bytes:
    # Only keep `alt` when it makes a difference, to share cached images
    alt = alt and _STATUS_COLORS[agent.status].alt_bg is not None
    return _render_agent_key_image(
        deck, agent.status, alt, _project_label(agent.project), agent.name
    )


# Returns the native image of a key showing nothing for `deck`.
@functools.cache
def blank_key_image(deck: StreamDeck) -> bytes:
    return PILHelper.to_native_key_format(deck, PILHelper.create_key_image(deck, '#000000'))
