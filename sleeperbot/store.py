"""Durable record of which transactions have already been announced.

Sleeper has no "give me what changed" endpoint — every poll returns the whole
week. Deduplication is therefore the bot's job, and it has to survive a restart:
without this file, every `docker compose up` would replay the week into the
channel.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections.abc import Iterable

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_transactions (
    transaction_id TEXT PRIMARY KEY,
    seen_at        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Sleeper only serves the current season's weeks, so ids older than this can
# never come back around. Pruning keeps the database in the low kilobytes.
RETENTION_DAYS = 400


class Store:
    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(path)
        # WAL survives an abrupt power cut better than the default journal,
        # which is the realistic failure mode for a Pi on a desk.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- announced transactions -------------------------------------------

    def unseen(self, transaction_ids: Iterable[str]) -> set[str]:
        ids = list(transaction_ids)
        if not ids:
            return set()
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT transaction_id FROM seen_transactions WHERE transaction_id IN ({placeholders})",
            ids,
        ).fetchall()
        return set(ids) - {row[0] for row in rows}

    def mark_seen(self, transaction_ids: Iterable[str]) -> None:
        now = int(time.time())
        self._conn.executemany(
            "INSERT OR IGNORE INTO seen_transactions (transaction_id, seen_at) VALUES (?, ?)",
            [(tid, now) for tid in transaction_ids],
        )
        self._conn.commit()

    def prune(self) -> None:
        cutoff = int(time.time()) - RETENTION_DAYS * 86400
        cursor = self._conn.execute(
            "DELETE FROM seen_transactions WHERE seen_at < ?", (cutoff,)
        )
        self._conn.commit()
        if cursor.rowcount:
            log.info("Pruned %d expired transaction ids", cursor.rowcount)

    # -- first-run flag ----------------------------------------------------

    def is_initialized(self) -> bool:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'initialized'"
        ).fetchone()
        return row is not None

    def mark_initialized(self) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('initialized', ?)",
            (str(int(time.time())),),
        )
        self._conn.commit()
