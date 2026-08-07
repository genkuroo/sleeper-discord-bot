#!/usr/bin/env python3
"""Look up your Sleeper league ids from your username.

The league id is not shown anywhere obvious in the Sleeper app — it is the long
number in the web URL, and the mobile app never shows a URL at all. This walks
the public API instead:

    username -> user_id -> leagues for a season

Usage:
    python scripts/find_league_id.py <sleeper-username> [season]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE_URL = "https://api.sleeper.app/v1"


def get(path: str):
    try:
        with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    username = sys.argv[1]
    # Default to the current season; Sleeper keys leagues by season year.
    season = sys.argv[2] if len(sys.argv) > 2 else (get("/state/nfl") or {}).get("season")

    user = get(f"/user/{username}")
    if not user:
        print(f"No Sleeper user called {username!r}.")
        return 1

    print(f"\n{username} → user_id {user['user_id']}\n")

    leagues = get(f"/user/{user['user_id']}/leagues/nfl/{season}") or []
    if not leagues:
        print(f"No NFL leagues found for the {season} season.")
        print("Try an earlier season, e.g.: find_league_id.py", username, "2025")
        return 1

    print(f"{season} NFL leagues:\n")
    for league in leagues:
        print(f"  {league['league_id']}   {league['name']}")
        print(f"  {'':>18}   {league.get('total_rosters')} teams · "
              f"{league.get('status')} · {league.get('season_type')}\n")

    print("Put the id you want in .env as SLEEPER_LEAGUE_ID.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
