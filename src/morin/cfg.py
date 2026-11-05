# SPDX-FileCopyrightText: 2026 Philippe Proulx <eeppeliteloop@gmail.com>
# SPDX-License-Identifier: MIT

import os
import tomllib
from pathlib import Path
from typing import Annotated

from PIL import Image, ImageColor
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from morin.herdr import AgentStatus


class CfgError(Exception):
    pass


# Returns the directory of the configuration file and of the link
# images, `~/.config/morin` by default.
def cfg_dir() -> Path:
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'morin'


def images_dir() -> Path:
    return cfg_dir() / 'images'


def _check_color(color: str) -> str:
    try:
        ImageColor.getrgb(color)
    except ValueError:
        raise ValueError(f'invalid color `{color}`') from None

    return color


# Returns the path of the image file `file_name` of the images
# directory, making sure that Pillow can read it.
def _image_path(file_name: Path) -> Path:
    path = images_dir() / file_name

    try:
        with Image.open(path) as image:
            image.verify()
    except FileNotFoundError:
        raise ValueError(f'no image file `{path}`') from None
    except (OSError, SyntaxError, ValueError) as exc:
        raise ValueError(f'cannot read the image file `{path}`: {exc}') from None

    return path


# Color which Pillow understands, for example `#fde047` or `black`.
type _Color = Annotated[str, AfterValidator(_check_color)]


# Fields use kebab-case names in the configuration file.
class _Model(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra='forbid',
        alias_generator=lambda name: name.replace('_', '-'),
        validate_by_name=True,
    )


# Key colors of an agent status.
class StatusColors(_Model):
    bg: _Color

    # Agent name, readable over `bg`.
    name: _Color

    # Background of the alternate phase of a blinking key, if any.
    alt_bg: _Color | None = None


# Key which opens a URL with Chromium.
class Link(_Model):
    url: str

    # Path of the key image file.
    #
    # In the configuration file: file name within the images directory.
    image: Annotated[Path, AfterValidator(_image_path)]


_DEF_AGENT_STATUS_COLORS = {
    AgentStatus.IDLE: StatusColors(bg='#000000', name='#ffffff'),
    AgentStatus.DONE: StatusColors(bg='#4ade80', name='#000000'),
    AgentStatus.WORKING: StatusColors(bg='#fde047', name='#000000', alt_bg='#d9a90a'),
    AgentStatus.BLOCKED: StatusColors(bg='#ff5a5a', name='#ffffff'),
    AgentStatus.UNKNOWN: StatusColors(bg='#475569', name='#ffffff'),
}


def _with_def_agent_status_colors(
    colors: dict[AgentStatus, StatusColors],
) -> dict[AgentStatus, StatusColors]:
    return _DEF_AGENT_STATUS_COLORS | colors


class Cfg(_Model):
    # Maximum number of agent keys, never covering a link key.
    #
    # `None` means all the keys but the bottom row.
    max_agent_keys: Annotated[int, Field(ge=1)] | None = None

    # Links of the bottom row of keys, from right to left.
    links: tuple[Link, ...] = ()

    # Short project names which fit a key.
    project_aliases: dict[str, str] = {}

    # Key colors of each agent status.
    #
    # A status missing from the configuration file keeps its
    # default colors.
    agent_status_colors: Annotated[
        dict[AgentStatus, StatusColors], AfterValidator(_with_def_agent_status_colors)
    ] = Field(default_factory=lambda: dict(_DEF_AGENT_STATUS_COLORS))

    def project_label(self, project_name: str) -> str:
        return self.project_aliases.get(project_name, project_name)


def _validation_error_msg(path: Path, exc: ValidationError) -> str:
    lines = [f'Invalid configuration file `{path}`:']

    for error in exc.errors():
        loc = '.'.join(str(part) for part in error['loc'])

        # Pydantic prefixes the messages of our validators
        msg = error['msg'].removeprefix('Value error, ')
        lines.append(f'  {loc}: {msg}')

    return '\n'.join(lines)


# Returns the configuration of the `config.toml` file of the
# configuration directory, or the default one without such a file.
#
# Raises `CfgError` on failure.
def load_cfg() -> Cfg:
    path = cfg_dir() / 'config.toml'

    try:
        with path.open('rb') as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return Cfg()
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CfgError(f'Cannot read `{path}`: {exc}') from exc

    try:
        return Cfg.model_validate(data)
    except ValidationError as exc:
        raise CfgError(_validation_error_msg(path, exc)) from None
