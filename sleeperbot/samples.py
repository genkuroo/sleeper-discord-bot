"""Sample payloads shaped exactly like Sleeper's.

Field names and nesting here match the live API — draft picks really do carry
three different roster ids, and scores really are split into whole and decimal
fields — so anything that renders these renders the real thing.

This lives in the package rather than under `tests/` because it has three
consumers, only one of which is the test suite: `scripts/preview.py` renders it
to the terminal, and `scripts/smoke_post.py` posts it to a Discord channel to
prove a deployment works. Shipping it in the image is what lets the smoke test
run inside the container, without shipping the tests themselves.
"""

from __future__ import annotations

import time

NOW_MS = int(time.time() * 1000)

USERS = [
    {"user_id": "u1", "display_name": "ethanf", "metadata": {"team_name": "Hurts Donut"}},
    {"user_id": "u2", "display_name": "jake_r", "metadata": {"team_name": "Kupp Noodles"}},
    {"user_id": "u3", "display_name": "marcus99", "metadata": {}},
    {"user_id": "u4", "display_name": "sam_t", "metadata": {"team_name": "Bijan Mustard"}},
]

ROSTERS = [
    {
        "roster_id": 1,
        "owner_id": "u1",
        "starters": ["4046", "6794", "7564"],
        "players": ["4046", "6794", "7564", "4034", "SF"],
        "settings": {"wins": 7, "losses": 3, "ties": 0, "fpts": 1284, "fpts_decimal": 56,
                     "fpts_against": 1150, "fpts_against_decimal": 12},
    },
    {
        "roster_id": 2,
        "owner_id": "u2",
        "starters": ["4034", "5849"],
        "players": ["4034", "5849"],
        "settings": {"wins": 6, "losses": 4, "ties": 0, "fpts": 1301, "fpts_decimal": 8,
                     "fpts_against": 1199, "fpts_against_decimal": 44},
    },
    {
        "roster_id": 3,
        "owner_id": "u3",
        "starters": ["1234"],
        "players": ["1234"],
        "settings": {"wins": 6, "losses": 4, "ties": 0, "fpts": 1150, "fpts_decimal": 0,
                     "fpts_against": 1240, "fpts_against_decimal": 90},
    },
    # An orphan roster — the manager quit mid-season and Sleeper leaves owner_id
    # null. The bot must still name it rather than crashing.
    {"roster_id": 4, "owner_id": None, "starters": [], "players": [],
     "settings": {"wins": 0, "losses": 10, "ties": 0, "fpts": 900, "fpts_decimal": 0}},
]

PLAYERS = {
    "4046": {"name": "Patrick Mahomes", "position": "QB", "team": "KC"},
    "6794": {"name": "Justin Jefferson", "position": "WR", "team": "MIN"},
    "7564": {"name": "Bijan Robinson", "position": "RB", "team": "ATL"},
    "4034": {"name": "Christian McCaffrey", "position": "RB", "team": "SF"},
    "5849": {"name": "Kyler Murray", "position": "QB", "team": "ARI"},
    "1234": {"name": "Travis Kelce", "position": "TE", "team": "KC"},
    "SF": {"name": "San Francisco 49ers", "position": "DEF", "team": "SF"},
}

FREE_AGENT_ADD = {
    "transaction_id": "t-free-agent",
    "type": "free_agent",
    "status": "complete",
    "status_updated": NOW_MS - 60_000,
    "roster_ids": [1],
    "adds": {"7564": 1},
    "drops": {"SF": 1},
    "settings": None,
}

WAIVER_CLAIM = {
    "transaction_id": "t-waiver",
    "type": "waiver",
    "status": "complete",
    "status_updated": NOW_MS - 120_000,
    "roster_ids": [2],
    "adds": {"5849": 2},
    "drops": None,
    "settings": {"waiver_bid": 27, "seq": 3},
}

# A league on waiver priority (waiver_type 0) rather than FAAB (waiver_type 2)
# produces claims with no waiver_bid at all — the budget field in league
# settings is an unused default there.
PRIORITY_WAIVER = {
    "transaction_id": "t-waiver-priority",
    "type": "waiver",
    "status": "complete",
    "status_updated": NOW_MS - 125_000,
    "roster_ids": [1],
    "adds": {"1234": 1},
    "drops": {"4046": 1},
    "settings": {"seq": 1},
}

FAILED_WAIVER = {
    "transaction_id": "t-waiver-failed",
    "type": "waiver",
    "status": "failed",
    "status_updated": NOW_MS - 130_000,
    "roster_ids": [3],
    "adds": {"5849": 3},
    "drops": None,
    "settings": {"waiver_bid": 12, "seq": 4},
    "metadata": {"notes": "Player is already on another roster"},
}

PENDING_WAIVER = {
    "transaction_id": "t-waiver-pending",
    "type": "waiver",
    "status": "processing",
    "status_updated": NOW_MS - 5_000,
    "roster_ids": [1],
    "adds": {"1234": 1},
    "settings": {"waiver_bid": 40},
}

TRADE = {
    "transaction_id": "t-trade",
    "type": "trade",
    "status": "complete",
    "status_updated": NOW_MS - 30_000,
    "roster_ids": [1, 2],
    "adds": {"4034": 1, "6794": 2},
    "drops": {"4034": 2, "6794": 1},
    "draft_picks": [
        # Roster 2 sends roster 1 a pick that originally belonged to roster 3.
        {"season": "2027", "round": 1, "roster_id": 3, "previous_owner_id": 2, "owner_id": 1},
        {"season": "2027", "round": 3, "roster_id": 1, "previous_owner_id": 1, "owner_id": 2},
    ],
    "waiver_budget": [{"sender": 1, "receiver": 2, "amount": 15}],
}

# Dynasty leagues trade picks with no players attached at all, often across
# several future seasons at once. The transaction then has an empty `adds`.
PICKS_ONLY_TRADE = {
    "transaction_id": "t-trade-picks",
    "type": "trade",
    "status": "complete",
    "status_updated": NOW_MS - 40_000,
    "roster_ids": [1, 3],
    "adds": None,
    "drops": None,
    "draft_picks": [
        {"season": "2027", "round": 1, "roster_id": 3, "previous_owner_id": 3, "owner_id": 1},
        {"season": "2028", "round": 2, "roster_id": 3, "previous_owner_id": 3, "owner_id": 1},
        {"season": "2027", "round": 2, "roster_id": 1, "previous_owner_id": 1, "owner_id": 3},
        {"season": "2027", "round": 11, "roster_id": 2, "previous_owner_id": 1, "owner_id": 3},
    ],
    "waiver_budget": [],
}

DROP_ONLY = {
    "transaction_id": "t-drop",
    "type": "free_agent",
    "status": "complete",
    "status_updated": NOW_MS - 200_000,
    "roster_ids": [3],
    "adds": None,
    "drops": {"1234": 3},
}

STALE = {
    "transaction_id": "t-stale",
    "type": "free_agent",
    "status": "complete",
    "status_updated": NOW_MS - 10 * 24 * 3600 * 1000,
    "roster_ids": [1],
    "adds": {"1234": 1},
}

ALL_TRANSACTIONS = [
    FREE_AGENT_ADD,
    WAIVER_CLAIM,
    FAILED_WAIVER,
    PENDING_WAIVER,
    TRADE,
    DROP_ONLY,
    STALE,
]
