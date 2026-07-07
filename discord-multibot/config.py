"""Channel configuration loading and lookup.

Phase 1 stores config in ``channels.yaml`` keyed by channel_id.

Phase 2 seam: all config access goes through :func:`get_channel_config`.
To move to a DB and/or key by ``(guild_id, channel_id)`` later, only this
module changes -- callers just ask for a channel's config by id.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "channels.yaml")


@dataclass(frozen=True)
class ChannelConfig:
    """Resolved config for a single channel."""

    mode: str          # "translate" | "chat"
    model: str         # OpenRouter model id
    trigger: str       # "auto" | "mention"
    enabled: bool = True
    system_override: Optional[str] = None


class _ConfigStore:
    """Abstraction over the config source.

    Phase 1 backing store is a yaml file loaded once at startup. Phase 2 can
    replace the internals (DB query, guild-aware keys) without touching the
    public ``get`` signature that the bot relies on.
    """

    def __init__(self, path: str = _CONFIG_PATH):
        self._path = path
        self._channels: dict[str, ChannelConfig] = {}
        self.reload()

    def reload(self) -> None:
        with open(self._path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        channels = raw.get("channels") or {}
        parsed: dict[str, ChannelConfig] = {}
        for channel_id, cfg in channels.items():
            cfg = cfg or {}
            parsed[str(channel_id)] = ChannelConfig(
                mode=cfg["mode"],
                model=cfg["model"],
                trigger=cfg["trigger"],
                enabled=cfg.get("enabled", True),
                system_override=cfg.get("system_override"),
            )
        self._channels = parsed

    def get(self, channel_id: int | str) -> Optional[ChannelConfig]:
        return self._channels.get(str(channel_id))


_store: Optional[_ConfigStore] = None


def _get_store() -> _ConfigStore:
    global _store
    if _store is None:
        _store = _ConfigStore()
    return _store


def get_channel_config(channel_id: int | str) -> Optional[ChannelConfig]:
    """Return the config for a channel, or None if the bot should ignore it.

    None is returned both for unregistered channels and (intentionally) is
    the signal the caller uses to skip the message entirely.
    """
    return _get_store().get(channel_id)
