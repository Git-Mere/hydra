"""Channel configuration store.

Config is set at runtime via the ``/setup`` slash command and persisted to a
local JSON file (``channel_config.json``) keyed by ``guild_id -> channel_id``.
There is no per-channel model any more: every channel uses a single model
fallback chain (``MODEL_CHAIN`` / ``DEFAULT_MODEL`` env vars).

All config access goes through this module. The store is loaded into memory at
startup and written back atomically (temp file + ``os.replace``) on every
change.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "channel_config.json")

# Ordered, speed-first default model fallback chain used for every channel.
# Fast, reliable models lead; reasoning is turned off/low per model (see
# MODEL_REASONING) so trivial translate/web-search turns don't waste seconds on
# hidden chain-of-thought. OpenRouter accepts at most three server-side fallback
# models per request, so the client batches this chain when calling
# chat/completions.
DEFAULT_MODEL_CHAIN = [
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-oss-20b:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-31b-it:free",
]

# Per-model reasoning body param (OpenRouter `reasoning` field). Verified facts:
#  - nvidia/nemotron-*: {"enabled": false} disables reasoning cleanly (~0.5s).
#  - openai/gpt-oss-*: reasoning is mandatory; {"enabled": false} 400s, so use
#    {"effort": "low"}.
#  - qwen / llama / gemma: leave unset (None) -- always safe.
# Any model id not listed here (e.g. from a MODEL_CHAIN override) maps to None.
MODEL_REASONING: dict[str, Optional[dict]] = {
    "nvidia/nemotron-3-nano-30b-a3b:free": {"enabled": False},
    "nvidia/nemotron-3-super-120b-a12b:free": {"enabled": False},
    "openai/gpt-oss-20b:free": {"effort": "low"},
    "qwen/qwen3-next-80b-a3b-instruct:free": None,
    "meta-llama/llama-3.3-70b-instruct:free": None,
    "google/gemma-4-31b-it:free": None,
}

# OpenRouter caps the server-side `models` fallback array at three per request.
MAX_FALLBACK_MODELS = 3

# Backward-compatible module-level fallback constant.
DEFAULT_MODEL = DEFAULT_MODEL_CHAIN[0]

# Allowed values, shared with the slash command choices.
MODES = ("translate", "websearch")
TRIGGERS = ("auto", "mention")

# Mode ids that have been renamed. Applied when loading persisted config so
# channels configured under the old name keep working.
_MODE_MIGRATIONS = {"chat": "websearch"}


def get_model_chain() -> list[str]:
    """Return the ordered model chain used for all channels."""
    env_chain = os.environ.get("MODEL_CHAIN")
    if env_chain is not None:
        chain = [model.strip() for model in env_chain.split(",") if model.strip()]
        return chain or list(DEFAULT_MODEL_CHAIN)

    env_default = os.environ.get("DEFAULT_MODEL")
    if env_default:
        return [env_default] + [model for model in DEFAULT_MODEL_CHAIN if model != env_default]

    return list(DEFAULT_MODEL_CHAIN)


def get_default_model() -> str:
    """Return the primary model id used for all channels."""
    return get_model_chain()[0]


def get_model_plan() -> list[dict]:
    """Return the model chain as an ordered list of reasoning-consistent batches.

    Consecutive chain models that share the same reasoning param are grouped
    together and each group is sliced to at most ``MAX_FALLBACK_MODELS`` models
    (OpenRouter's fallback-array cap). Because reasoning is a per-request body
    field, every model within one batch must share the same setting or a
    fallback model could 400. Each batch is ``{"models": [...], "reasoning":
    {..}|None}``. Unknown model ids (from a MODEL_CHAIN/DEFAULT_MODEL override)
    map to ``None`` reasoning, which is always safe.
    """
    plan: list[dict] = []
    for model in get_model_chain():
        reasoning = MODEL_REASONING.get(model)
        if (
            plan
            and plan[-1]["reasoning"] == reasoning
            and len(plan[-1]["models"]) < MAX_FALLBACK_MODELS
        ):
            plan[-1]["models"].append(model)
        else:
            plan.append({"models": [model], "reasoning": reasoning})
    return plan


@dataclass(frozen=True)
class ChannelConfig:
    """Resolved config for a single channel."""

    mode: str          # "translate" | "websearch"
    trigger: str       # "auto" | "mention"
    enabled: bool = True


class JsonStore:
    """JSON-backed config store keyed by (guild_id, channel_id).

    On-disk shape::

        {"<guild_id>": {"<channel_id>": {"mode": ..., "trigger": ..., "enabled": ...}}}

    A missing or corrupt file is treated as an empty store so the bot always
    starts. Every mutation is persisted atomically.
    """

    def __init__(self, path: str = _CONFIG_PATH):
        self._path = path
        # guild_id -> channel_id -> ChannelConfig  (keys are strings)
        self._guilds: dict[str, dict[str, ChannelConfig]] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            self._guilds = {}
            return
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Config file %s unreadable (%s); starting empty", self._path, exc)
            self._guilds = {}
            return

        parsed: dict[str, dict[str, ChannelConfig]] = {}
        if isinstance(raw, dict):
            for guild_id, channels in raw.items():
                if not isinstance(channels, dict):
                    continue
                parsed_channels: dict[str, ChannelConfig] = {}
                for channel_id, cfg in channels.items():
                    if not isinstance(cfg, dict) or "mode" not in cfg or "trigger" not in cfg:
                        continue
                    # Migrate renamed mode ids (e.g. legacy "chat" -> "websearch")
                    # so channels configured before the rename keep working.
                    mode = _MODE_MIGRATIONS.get(cfg["mode"], cfg["mode"])
                    parsed_channels[str(channel_id)] = ChannelConfig(
                        mode=mode,
                        trigger=cfg["trigger"],
                        enabled=bool(cfg.get("enabled", True)),
                    )
                if parsed_channels:
                    parsed[str(guild_id)] = parsed_channels
        self._guilds = parsed

    def _save(self) -> None:
        serialisable = {
            guild_id: {
                channel_id: {
                    "mode": cfg.mode,
                    "trigger": cfg.trigger,
                    "enabled": cfg.enabled,
                }
                for channel_id, cfg in channels.items()
            }
            for guild_id, channels in self._guilds.items()
        }
        directory = os.path.dirname(self._path) or "."
        # Write to a temp file in the same directory, then atomically replace.
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".channel_config.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(serialisable, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except BaseException:
            # Don't leave a stray temp file behind on failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get(self, guild_id: int | str, channel_id: int | str) -> Optional[ChannelConfig]:
        return self._guilds.get(str(guild_id), {}).get(str(channel_id))

    def set(
        self,
        guild_id: int | str,
        channel_id: int | str,
        mode: str,
        trigger: str,
        enabled: bool = True,
    ) -> ChannelConfig:
        """Create or replace a channel's config and persist. Returns the config."""
        cfg = ChannelConfig(mode=mode, trigger=trigger, enabled=enabled)
        self._guilds.setdefault(str(guild_id), {})[str(channel_id)] = cfg
        self._save()
        return cfg

    def disable(self, guild_id: int | str, channel_id: int | str) -> bool:
        """Mark a channel disabled (kept on disk). Returns True if it existed."""
        channels = self._guilds.get(str(guild_id))
        if not channels or str(channel_id) not in channels:
            return False
        existing = channels[str(channel_id)]
        channels[str(channel_id)] = ChannelConfig(
            mode=existing.mode, trigger=existing.trigger, enabled=False
        )
        self._save()
        return True


_store: Optional[JsonStore] = None


def _get_store() -> JsonStore:
    global _store
    if _store is None:
        _store = JsonStore()
    return _store


def get_channel_config(guild_id: int | str, channel_id: int | str) -> Optional[ChannelConfig]:
    """Return the config for a channel, or None if the bot should ignore it.

    None is returned for unregistered channels and is the signal the caller
    uses to skip the message entirely.
    """
    return _get_store().get(guild_id, channel_id)


def set_channel_config(
    guild_id: int | str, channel_id: int | str, mode: str, trigger: str
) -> ChannelConfig:
    """Enable and configure a channel (called by /setup)."""
    return _get_store().set(guild_id, channel_id, mode, trigger, enabled=True)


def disable_channel(guild_id: int | str, channel_id: int | str) -> bool:
    """Disable a channel (called by /setup-off). True if it existed."""
    return _get_store().disable(guild_id, channel_id)
