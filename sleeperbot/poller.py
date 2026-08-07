"""Decides which transactions are worth announcing.

Kept separate from the bot so the rules — which weeks to look at, what counts
as "new", what gets suppressed — are plain functions over dictionaries and can
be exercised without a Discord connection.
"""

from __future__ import annotations

import time

# Sleeper indexes transactions by "round", which is the NFL week. Week 0 is the
# preseason bucket where offseason adds and drops land.
MIN_WEEK = 0
MAX_WEEK = 18

ANNOUNCED_TYPES = {"free_agent", "waiver", "trade", "commissioner"}


def weeks_to_scan(state: dict) -> list[int]:
    """Weeks worth polling right now.

    Scanning a three-week window instead of just the current week costs two
    extra requests and covers the two cases where a transaction lands in a
    neighbouring bucket: waivers that process early Wednesday morning can be
    filed against the upcoming week, and a move made minutes before the weekly
    rollover stays in the old one.
    """
    week = state.get("week")
    if not isinstance(week, int):
        week = 1

    candidates = {week - 1, week, week + 1}
    return sorted(w for w in candidates if MIN_WEEK <= w <= MAX_WEEK)


def is_announceable(txn: dict, alert_failed_waivers: bool) -> bool:
    if txn.get("type") not in ANNOUNCED_TYPES:
        return False

    status = txn.get("status")
    if status == "complete":
        return True
    # A claim sitting in "processing" has not happened yet — announcing it would
    # spoil waivers before they run, and the same transaction_id comes back as
    # "complete" once it does.
    if status == "failed":
        return alert_failed_waivers
    return False


def select_new(
    transactions: list[dict],
    known_ids: set[str],
    *,
    alert_failed_waivers: bool,
    max_backfill_hours: int,
    now_ms: float | None = None,
) -> list[dict]:
    """Filter a week's transactions down to what should be posted, in order."""
    if now_ms is None:
        now_ms = time.time() * 1000
    cutoff_ms = now_ms - max_backfill_hours * 3600 * 1000

    selected = []
    for txn in transactions:
        txn_id = txn.get("transaction_id")
        if not txn_id or txn_id in known_ids:
            continue
        if not is_announceable(txn, alert_failed_waivers):
            continue
        updated = txn.get("status_updated") or txn.get("created") or 0
        if updated < cutoff_ms:
            # Old enough that announcing it now would be confusing rather than
            # informative — the bot was down, and the league already knows.
            continue
        selected.append(txn)

    # Oldest first, so a batch reads in the order things actually happened.
    selected.sort(key=lambda t: t.get("status_updated") or t.get("created") or 0)
    return selected


def dedupe(transactions: list[dict]) -> list[dict]:
    """Collapse the overlap created by scanning neighbouring weeks."""
    seen: set[str] = set()
    unique = []
    for txn in transactions:
        txn_id = txn.get("transaction_id")
        if not txn_id or txn_id in seen:
            continue
        seen.add(txn_id)
        unique.append(txn)
    return unique
