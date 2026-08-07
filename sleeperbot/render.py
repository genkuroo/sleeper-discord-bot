"""Formats Sleeper transactions as Discord embeds.

One function per transaction shape, because the shapes genuinely differ. An add
or drop is a one-sided event and reads best as prose; a trade is a table of who
gets what and reads best as one embed field per team.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from .league import LeagueContext

COLOR_ADD = 0x2ECC71  # green
COLOR_DROP = 0xE67E22  # orange — a drop with no add is not good news
COLOR_TRADE = 0x5865F2  # blurple
COLOR_FAILED = 0x95A5A6  # grey
COLOR_COMMISH = 0xF1C40F  # gold

# Discord rejects a field value over 1024 characters outright, which would turn
# a 12-player trade into a dropped alert.
MAX_FIELD_CHARS = 1024


def render_transaction(
    txn: dict, ctx: LeagueContext, league_name: str
) -> discord.Embed | None:
    """Build the embed for one transaction, or None if it should not be posted."""
    txn_type = txn.get("type")

    if txn_type == "trade":
        embed = _render_trade(txn, ctx)
    elif txn_type in {"free_agent", "waiver", "commissioner"}:
        embed = _render_roster_move(txn, ctx)
    else:
        return None

    if embed is None:
        return None

    embed.timestamp = _timestamp(txn)
    embed.set_footer(text=league_name)
    return embed


# -- roster moves (adds, drops, waiver claims) ----------------------------


def _render_roster_move(txn: dict, ctx: LeagueContext) -> discord.Embed | None:
    adds = txn.get("adds") or {}
    drops = txn.get("drops") or {}
    if not adds and not drops:
        return None

    failed = txn.get("status") == "failed"
    is_waiver = txn.get("type") == "waiver"
    is_commish = txn.get("type") == "commissioner"

    if failed:
        title, color = "❌ Failed Waiver Claim", COLOR_FAILED
    elif is_commish:
        title, color = "🛠️ Commissioner Move", COLOR_COMMISH
    elif is_waiver:
        title, color = "💰 Waiver Claim", COLOR_ADD
    elif adds:
        title, color = "🆓 Free Agent Pickup", COLOR_ADD
    else:
        title, color = "✂️ Drop", COLOR_DROP

    lines = []
    for roster_id in _rosters_involved(adds, drops):
        team = ctx.team_name(roster_id)
        added = [ctx.player_name(pid) for pid, rid in adds.items() if rid == roster_id]
        dropped = [ctx.player_name(pid) for pid, rid in drops.items() if rid == roster_id]

        clauses = []
        if added:
            clauses.append("**+** " + ", ".join(added))
        if dropped:
            clauses.append("**−** " + ", ".join(dropped))
        lines.append(f"**{team}**\n" + "\n".join(clauses))

    embed = discord.Embed(title=title, description="\n\n".join(lines), color=color)

    bid = (txn.get("settings") or {}).get("waiver_bid")
    if bid is not None:
        embed.add_field(name="FAAB bid", value=f"${bid}", inline=True)

    # Sleeper puts the human-readable failure reason here — "Insufficient
    # funds", "Player already rostered" — which is the whole point of showing
    # a failed claim at all.
    notes = (txn.get("metadata") or {}).get("notes")
    if failed and notes:
        embed.add_field(name="Reason", value=_truncate(notes), inline=False)

    return embed


# -- trades ---------------------------------------------------------------


def _render_trade(txn: dict, ctx: LeagueContext) -> discord.Embed | None:
    adds = txn.get("adds") or {}
    picks = txn.get("draft_picks") or []
    budget = txn.get("waiver_budget") or []

    # Sleeper models a trade as "here is where everything ended up", so the
    # readable framing is per-receiver: for each team, what did it end up with?
    incoming: dict[int, list[str]] = {}

    for player_id, roster_id in adds.items():
        incoming.setdefault(roster_id, []).append(ctx.player_name(player_id))

    for pick in picks:
        receiver = pick.get("owner_id")
        origin = pick.get("roster_id")
        label = f"{pick.get('season')} {_ordinal(pick.get('round'))} round pick"
        # A pick that did not originate with the team receiving it is worth
        # naming — "2027 1st (from Jake)" is the detail leagues argue about.
        if origin is not None and origin != receiver:
            label += f" (from {ctx.team_name(origin)})"
        incoming.setdefault(receiver, []).append(label)

    for entry in budget:
        receiver = entry.get("receiver")
        amount = entry.get("amount")
        sender = ctx.team_name(entry.get("sender"))
        incoming.setdefault(receiver, []).append(f"${amount} FAAB (from {sender})")

    if not incoming:
        return None

    # Fall back to roster_ids so a team that only gave things up still appears.
    for roster_id in txn.get("roster_ids") or []:
        incoming.setdefault(roster_id, ["*Nothing*"])

    embed = discord.Embed(title="🔁 Trade", color=COLOR_TRADE)
    for roster_id in sorted(incoming):
        embed.add_field(
            name=f"{ctx.team_name(roster_id)} receives",
            value=_truncate("\n".join(f"• {item}" for item in incoming[roster_id])),
            inline=False,
        )
    return embed


# -- helpers --------------------------------------------------------------


def _rosters_involved(adds: dict, drops: dict) -> list[int]:
    return sorted({*adds.values(), *drops.values()})


def _ordinal(number) -> str:
    try:
        number = int(number)
    except (TypeError, ValueError):
        return "?"
    # 11th/12th/13th are the exceptions to the 1st/2nd/3rd rule.
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _truncate(text: str, limit: int = MAX_FIELD_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _timestamp(txn: dict) -> datetime | None:
    raw = txn.get("status_updated") or txn.get("created")
    if not raw:
        return None
    # Sleeper reports epoch milliseconds.
    return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
