"""Shared update logic used by the NeonVeil Update Manager and CLI."""

from .core import (
    CHANNELS,
    DEFAULT_REPOSITORY,
    UpdateError,
    UpdateManager,
    UpdateRelease,
    compare_versions,
    infer_channel,
    parse_version,
)

__all__ = [
    "CHANNELS",
    "DEFAULT_REPOSITORY",
    "UpdateError",
    "UpdateManager",
    "UpdateRelease",
    "compare_versions",
    "infer_channel",
    "parse_version",
]
