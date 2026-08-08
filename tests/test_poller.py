"""Tests for the rules that decide what gets announced.

These are the rules that cause visible damage when wrong: a bot that re-posts
history, spoils waivers before they process, or silently drops a trade.
"""

from __future__ import annotations

import time

import pytest

from sleeperbot.poller import dedupe, is_announceable, select_new, weeks_to_scan
from sleeperbot import samples as fixtures


def _select(transactions, known=None, **kwargs):
    options = {"alert_failed_waivers": False, "max_backfill_hours": 48}
    options.update(kwargs)
    return select_new(transactions, known or set(), **options)


# -- week window ----------------------------------------------------------


@pytest.mark.parametrize(
    "week,expected",
    [
        (0, [0, 1]),          # preseason: there is no week -1
        (1, [0, 1, 2]),
        (9, [8, 9, 10]),
        (18, [17, 18]),       # end of season: nothing past week 18
    ],
)
def test_weeks_to_scan_stays_in_range(week, expected):
    assert weeks_to_scan({"week": week}) == expected


def test_weeks_to_scan_survives_a_missing_week():
    # The state endpoint has returned odd values in the offseason before.
    assert weeks_to_scan({}) == [0, 1, 2]
    assert weeks_to_scan({"week": None}) == [0, 1, 2]


# -- announceability ------------------------------------------------------


def test_pending_waiver_is_never_announced():
    """Posting a claim before waivers run would spoil the bid."""
    assert is_announceable(fixtures.PENDING_WAIVER, alert_failed_waivers=True) is False


def test_failed_waiver_respects_the_setting():
    assert is_announceable(fixtures.FAILED_WAIVER, alert_failed_waivers=False) is False
    assert is_announceable(fixtures.FAILED_WAIVER, alert_failed_waivers=True) is True


def test_completed_moves_are_announced():
    for txn in (fixtures.FREE_AGENT_ADD, fixtures.WAIVER_CLAIM, fixtures.TRADE):
        assert is_announceable(txn, alert_failed_waivers=False) is True


# -- selection ------------------------------------------------------------


def test_already_seen_transactions_are_not_repeated():
    known = {"t-free-agent", "t-waiver", "t-trade", "t-drop"}
    assert _select(fixtures.ALL_TRANSACTIONS, known) == []


def test_selection_picks_only_new_completed_moves():
    selected = _select(fixtures.ALL_TRANSACTIONS)
    ids = [t["transaction_id"] for t in selected]

    assert "t-free-agent" in ids
    assert "t-waiver" in ids
    assert "t-trade" in ids
    assert "t-drop" in ids
    assert "t-waiver-pending" not in ids  # still processing
    assert "t-waiver-failed" not in ids   # suppressed by default
    assert "t-stale" not in ids           # older than the backfill window


def test_selection_is_chronological():
    """A batch should read in the order the moves actually happened."""
    selected = _select(fixtures.ALL_TRANSACTIONS)
    stamps = [t["status_updated"] for t in selected]
    assert stamps == sorted(stamps)


def test_backfill_window_suppresses_old_moves():
    """The Pi being off for a week must not dump the backlog into chat."""
    recent = _select([fixtures.STALE], max_backfill_hours=48)
    assert recent == []

    # Widen the window past the fixture's age and it comes back.
    generous = _select([fixtures.STALE], max_backfill_hours=24 * 30)
    assert len(generous) == 1


def test_transaction_without_an_id_is_ignored():
    assert _select([{"type": "free_agent", "status": "complete",
                     "status_updated": time.time() * 1000}]) == []


# -- dedupe ---------------------------------------------------------------


def test_dedupe_collapses_the_overlapping_week_window():
    """Scanning weeks 8, 9 and 10 returns week 9's moves more than once."""
    doubled = fixtures.ALL_TRANSACTIONS + fixtures.ALL_TRANSACTIONS
    assert len(dedupe(doubled)) == len(fixtures.ALL_TRANSACTIONS)
