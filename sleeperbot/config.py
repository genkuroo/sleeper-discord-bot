"""Environment-driven configuration.

Everything the bot needs comes from the environment, never from a committed
file. That keeps the same image usable for any league — the Pi's `.env` is the
only thing that differs between deployments.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is missing or unusable."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required but not set")
    return value


def _require_int(name: str) -> int:
    raw = _require(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int
    channel_id: int
    league_id: str

    poll_seconds: int
    # A transaction older than this is never posted, even if the bot has never
    # seen it. Without this, a Pi that was off for a week would wake up and dump
    # every missed move into the channel at once.
    max_backfill_hours: int
    # Failed waiver claims are noisy — every losing bid on a popular player is
    # its own event — so they are off unless the league actually wants them.
    alert_failed_waivers: bool

    data_dir: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            discord_token=_require("DISCORD_TOKEN"),
            guild_id=_require_int("DISCORD_GUILD_ID"),
            channel_id=_require_int("DISCORD_CHANNEL_ID"),
            league_id=_require("SLEEPER_LEAGUE_ID"),
            poll_seconds=_int("POLL_SECONDS", 300),
            max_backfill_hours=_int("MAX_BACKFILL_HOURS", 48),
            alert_failed_waivers=_bool("ALERT_FAILED_WAIVERS", False),
            data_dir=os.environ.get("DATA_DIR", "/data").strip() or "/data",
            log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        )
