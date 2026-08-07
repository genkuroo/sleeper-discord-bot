"""Async client for the public Sleeper API.

Sleeper's read API needs no key and no account — every endpoint here is a plain
GET. The interesting work is caching. The endpoints fall into three tiers:

  * ``/players/nfl``  ~5 MB, ~11k entries. Sleeper explicitly asks callers to
    fetch it at most once a day, so it is cached to disk and trimmed to the
    three fields we actually render.
  * league / users     Change rarely (a team rename). Cached in memory for an
                       hour.
  * transactions       Never cached. This is the thing we are watching.

Docs: https://docs.sleeper.com/
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://api.sleeper.app/v1"

# Sleeper allows roughly 1000 calls/minute. A five-minute poll of three weeks
# uses about 1, so these values exist to be polite under failure, not to ration.
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
REQUEST_TIMEOUT = 30

PLAYER_CACHE_TTL = 24 * 60 * 60
LEAGUE_CACHE_TTL = 60 * 60
ROSTER_CACHE_TTL = 60


class SleeperError(RuntimeError):
    """A Sleeper request failed after exhausting retries."""


class _TTLCache:
    """Tiny single-value cache. Not thread-safe; the bot is single-threaded."""

    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._value: Any = None
        self._fetched_at = 0.0

    def get(self) -> Any | None:
        if self._value is None or time.monotonic() - self._fetched_at > self._ttl:
            return None
        return self._value

    def set(self, value: Any) -> None:
        self._value = value
        self._fetched_at = time.monotonic()

    def clear(self) -> None:
        self._value = None


class SleeperClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        league_id: str,
        cache_dir: str,
    ) -> None:
        self._session = session
        self._league_id = league_id
        self._player_cache_path = os.path.join(cache_dir, "players.json")

        self._state_cache = _TTLCache(ROSTER_CACHE_TTL)
        self._league_cache = _TTLCache(LEAGUE_CACHE_TTL)
        self._users_cache = _TTLCache(LEAGUE_CACHE_TTL)
        self._rosters_cache = _TTLCache(ROSTER_CACHE_TTL)
        self._players: dict[str, dict[str, str]] | None = None
        self._players_loaded_at = 0.0

    # -- transport ---------------------------------------------------------

    async def _get(self, path: str) -> Any:
        """GET a Sleeper path, retrying transient failures with backoff.

        Sleeper returns 200 with a literal ``null`` body for a valid-but-empty
        resource (a week with no transactions, most commonly), so callers get
        ``None`` rather than an exception for that case.
        """
        url = f"{BASE_URL}{path}"
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                async with self._session.get(url, timeout=timeout) as response:
                    if response.status == 404:
                        return None
                    # 429 and 5xx are worth retrying; 4xx means we asked wrong.
                    if response.status == 429 or response.status >= 500:
                        raise SleeperError(f"{response.status} from {url}")
                    response.raise_for_status()
                    return await response.json(content_type=None)
            except (aiohttp.ClientError, SleeperError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2**attempt)
                    log.warning(
                        "Sleeper GET %s failed (%s), retrying in %.0fs", path, exc, delay
                    )
                    await asyncio.sleep(delay)

        raise SleeperError(f"GET {path} failed after {MAX_RETRIES} attempts: {last_error}")

    # -- league metadata ---------------------------------------------------

    async def state(self) -> dict:
        """Current NFL season state — most importantly, the active week."""
        cached = self._state_cache.get()
        if cached is None:
            cached = await self._get("/state/nfl") or {}
            self._state_cache.set(cached)
        return cached

    async def league(self) -> dict:
        cached = self._league_cache.get()
        if cached is None:
            cached = await self._get(f"/league/{self._league_id}")
            if cached is None:
                raise SleeperError(
                    f"League {self._league_id} not found. Check SLEEPER_LEAGUE_ID."
                )
            self._league_cache.set(cached)
        return cached

    async def users(self) -> list[dict]:
        cached = self._users_cache.get()
        if cached is None:
            cached = await self._get(f"/league/{self._league_id}/users") or []
            self._users_cache.set(cached)
        return cached

    async def rosters(self) -> list[dict]:
        cached = self._rosters_cache.get()
        if cached is None:
            cached = await self._get(f"/league/{self._league_id}/rosters") or []
            self._rosters_cache.set(cached)
        return cached

    async def transactions(self, week: int) -> list[dict]:
        return await self._get(f"/league/{self._league_id}/transactions/{week}") or []

    async def matchups(self, week: int) -> list[dict]:
        return await self._get(f"/league/{self._league_id}/matchups/{week}") or []

    # -- players -----------------------------------------------------------

    async def players(self) -> dict[str, dict[str, str]]:
        """Player id -> {name, position, team}, cached on disk for a day.

        The upstream payload is ~5 MB of mostly-unused fields (college, height,
        rotowire ids...). Trimming before it hits disk keeps the cache around
        1 MB, which matters on a Pi where this process shares 4 GB with five
        other containers.
        """
        if self._players is not None and time.monotonic() - self._players_loaded_at < PLAYER_CACHE_TTL:
            return self._players

        cached = self._load_players_from_disk()
        if cached is not None:
            self._players = cached
            self._players_loaded_at = time.monotonic()
            return cached

        log.info("Fetching the Sleeper player catalog (~5 MB, once per day)")
        raw = await self._get("/players/nfl") or {}
        trimmed = {pid: _trim_player(pid, data) for pid, data in raw.items()}

        self._players = trimmed
        self._players_loaded_at = time.monotonic()
        self._save_players_to_disk(trimmed)
        log.info("Cached %d players", len(trimmed))
        return trimmed

    def _load_players_from_disk(self) -> dict[str, dict[str, str]] | None:
        try:
            age = time.time() - os.path.getmtime(self._player_cache_path)
            if age > PLAYER_CACHE_TTL:
                return None
            with open(self._player_cache_path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            # A missing or half-written cache is not an error — just refetch.
            return None

    def _save_players_to_disk(self, players: dict) -> None:
        # Write-then-rename so a crash mid-write can't leave a truncated cache
        # that the next boot would happily load.
        tmp_path = f"{self._player_cache_path}.tmp"
        try:
            os.makedirs(os.path.dirname(self._player_cache_path), exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(players, handle, separators=(",", ":"))
            os.replace(tmp_path, self._player_cache_path)
        except OSError as exc:
            log.warning("Could not write the player cache: %s", exc)


def _trim_player(player_id: str, data: dict) -> dict[str, str]:
    """Reduce a Sleeper player record to what an alert actually shows."""
    name = data.get("full_name")
    if not name:
        # Team defenses have no full_name; they arrive as first="San Francisco",
        # last="49ers" under a player_id that is the team abbreviation.
        parts = [data.get("first_name") or "", data.get("last_name") or ""]
        name = " ".join(part for part in parts if part).strip()
    return {
        "name": name or player_id,
        "position": data.get("position") or "",
        "team": data.get("team") or "FA",
    }
