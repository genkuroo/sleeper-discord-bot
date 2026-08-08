#!/usr/bin/env python3
"""Print the alerts as they would appear, without touching Discord.

Two modes:

    python scripts/preview.py                  # bundled sample league
    python scripts/preview.py --league <id>    # your real league, last 3 weeks

Neither needs a bot token. The second only reads Sleeper's public API, so it is
the fastest way to confirm a league id is right before deploying anything.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sleeperbot.league import LeagueContext, load_context  # noqa: E402
from sleeperbot.poller import dedupe, is_announceable, weeks_to_scan  # noqa: E402
from sleeperbot.render import render_transaction  # noqa: E402
from sleeperbot.sleeper import SleeperClient  # noqa: E402

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"


def show(embed) -> None:
    """Approximate Discord's embed layout in the terminal."""
    if embed is None:
        return
    print(f"  ┌─ {BOLD}{embed.title}{RESET}")
    for line in (embed.description or "").splitlines():
        print(f"  │  {line}")
    for field in embed.fields:
        print(f"  │  {CYAN}{field.name}{RESET}")
        for line in str(field.value).splitlines():
            print(f"  │    {line}")
    stamp = embed.timestamp.strftime("%Y-%m-%d %H:%M UTC") if embed.timestamp else ""
    print(f"  └─ {DIM}{embed.footer.text or ''} · {stamp}{RESET}\n")


def show_teams(ctx) -> None:
    """Print the roster_id -> team name join the alerts depend on.

    This is the lookup that makes an alert say "Hurts Donut" instead of
    "Roster 3", so seeing your league's real names here confirms both the
    league id and the users/rosters join in one look.
    """
    print(f"{BOLD}Teams the bot resolved{RESET}\n")
    for roster in sorted(ctx.rosters, key=lambda r: r.get("roster_id") or 0):
        roster_id = roster.get("roster_id")
        count = len(roster.get("players") or [])
        detail = f"{count} players" if count else "no players yet (predraft)"
        print(f"  {roster_id:>2}  {ctx.team_name(roster_id):<24} {DIM}{detail}{RESET}")
    print(f"\n{DIM}Names look right? Then SLEEPER_LEAGUE_ID is correct.{RESET}\n")


def preview_fixtures() -> None:
    from sleeperbot import samples as fixtures

    ctx = LeagueContext(
        rosters=fixtures.ROSTERS, users=fixtures.USERS, players=fixtures.PLAYERS
    )

    print(f"\n{BOLD}Sample league — what the channel would show{RESET}\n")
    for txn in fixtures.ALL_TRANSACTIONS:
        if is_announceable(txn, alert_failed_waivers=True):
            show(render_transaction(txn, ctx, "Sample Dynasty League"))

    print(f"{DIM}Suppressed by default:{RESET}")
    print(f"{DIM}  · t-waiver-pending — still processing, would spoil the bid{RESET}")
    print(f"{DIM}  · t-waiver-failed  — shown above; set ALERT_FAILED_WAIVERS=1 to keep it{RESET}")
    print(f"{DIM}  · t-stale          — older than MAX_BACKFILL_HOURS{RESET}\n")


async def preview_league(league_id: str) -> None:
    import aiohttp

    with tempfile.TemporaryDirectory() as cache_dir:
        async with aiohttp.ClientSession() as session:
            client = SleeperClient(session, league_id, cache_dir)

            league = await client.league()
            print(f"\n{BOLD}{league.get('name')}{RESET} "
                  f"({league.get('season')}, {league.get('total_rosters')} teams)\n")

            state = await client.state()
            weeks = weeks_to_scan(state)
            print(f"{DIM}Scanning weeks {weeks}…{RESET}\n")

            raw: list[dict] = []
            for week in weeks:
                raw.extend(await client.transactions(week))
            transactions = dedupe(raw)

            announceable = [
                t for t in transactions if is_announceable(t, alert_failed_waivers=True)
            ]
            announceable.sort(key=lambda t: t.get("status_updated") or 0)

            ctx = await load_context(client)

            if not announceable:
                # Predraft and early preseason leagues have nothing to render,
                # which would leave a misconfigured league id looking identical
                # to a quiet one. Show the roster join instead: if the team
                # names below are your league's, the config is right.
                print("No transactions in these weeks yet — expected before the draft.\n")
                show_teams(ctx)
                return

            for txn in announceable:
                show(render_transaction(txn, ctx, league.get("name", "League")))

            print(f"{DIM}{len(announceable)} transaction(s) across weeks {weeks}.{RESET}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", help="Sleeper league id (omit for sample data)")
    args = parser.parse_args()

    if args.league:
        asyncio.run(preview_league(args.league))
    else:
        preview_fixtures()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
