# SPDX-FileCopyrightText: 2026 Philippe Proulx <eeppeliteloop@gmail.com>
# SPDX-License-Identifier: MIT

import functools
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from StreamDeck.Devices.StreamDeck import StreamDeck
from StreamDeck.ImageHelpers import PILHelper

from morin.cfg import Cfg, StatusColors
from morin.herdr import Agent

type _Font = ImageFont.FreeTypeFont | ImageFont.ImageFont

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


# Only takes what affects the rendering, so that agents sharing it share
# the cached image.
@functools.lru_cache(maxsize=256)
def _render_agent_key_image(
    deck: StreamDeck, colors: StatusColors, alt: bool, project_label: str, name: str
) -> bytes:
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
# for StreamDeck.set_key_image(), with the colors and project aliases
# of `cfg`.
#
# `alt` selects the alternate phase of a blinking key.
def agent_key_image(deck: StreamDeck, agent: Agent, cfg: Cfg, alt: bool = False) -> bytes:
    colors = cfg.agent_status_colors[agent.status]

    # Only keep `alt` when it makes a difference, to share cached images
    alt = alt and colors.alt_bg is not None
    return _render_agent_key_image(deck, colors, alt, cfg.project_label(agent.project), agent.name)


# Returns the native key image for `deck` showing the image file
# `path`, scaled and cropped to fill the key.
@functools.cache
def picture_key_image(deck: StreamDeck, path: Path) -> bytes:
    with Image.open(path) as file_image:
        image = file_image.convert('RGB')

    return PILHelper.to_native_key_format(
        deck, ImageOps.fit(image, deck.key_image_format()['size'], Image.Resampling.LANCZOS)
    )


# Returns the native image of a key showing nothing for `deck`.
@functools.cache
def blank_key_image(deck: StreamDeck) -> bytes:
    return PILHelper.to_native_key_format(deck, PILHelper.create_key_image(deck, '#000000'))
