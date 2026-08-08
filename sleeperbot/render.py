"""Formats Sleeper transactions as Discord embeds.

One function per transaction shape, because the shapes genuinely differ. An add
or drop is a one-sided event and reads best as prose; a trade is a table of who
gets what and reads best as one embed field per team.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from .league import LeagueContext

# Discord embeds cannot colour arbitrary text — the only mechanism for that is
# an ANSI code block, which renders grey and monospaced on mobile and would be
# worse than plain text for a league that reads this on phones. Coloured emoji
# render identically on every client, so that is how add and drop are marked.
MARKER_ADD = "🟢"
MARKER_DROP = "🔴"
# Picks and budget get their own markers so a dynasty column that is mostly
# picks is still scannable against the players in it.
MARKER_PICK = "🎟️"
MARKER_FAAB = "💵"

# The event type lives in the description as a `##` header rather than in the
# embed's own title, because a description header renders *larger* than the
# title does. Discord has no horizontal-rule markdown, so the divider under it
# is drawn with box characters.
DIVIDER = "━━━━━━━━━━━━━━━━━━"

# Trades read as columns of what each side received, with an arrow between the
# two-team case. Note Discord collapses inline fields to full width on narrow
# screens, so this degrades to a stacked list on phones rather than breaking.
TRADE_ARROW = "⇄"
MAX_TRADE_COLUMNS = 3

# A short rule under each team name, drawn inside the field value. The team
# name itself stays in the field *name*: field names render bold natively, and
# markdown headers are ignored in both field names and field values, so moving
# the name into the value would only make it smaller.
COLUMN_RULE = "▬▬▬▬▬▬"

# Discord rejects an empty field value, so the separator column holds a
# zero-width space instead of nothing.
ZERO_WIDTH = "​"

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

    rosters = _rosters_involved(adds, drops)
    # The author line holds exactly one name, so it can only carry the team when
    # a single roster is involved — which is every waiver, add and drop. A
    # commissioner move touching several rosters falls back to inline headers.
    solo = rosters[0] if len(rosters) == 1 else None

    blocks = []
    for roster_id in rosters:
        added = [pid for pid, rid in adds.items() if rid == roster_id]
        dropped = [pid for pid, rid in drops.items() if rid == roster_id]

        lines = [] if solo else [f"### {ctx.team_name(roster_id)}"]

        # The added player is the news, so it carries the weight. A drop with
        # nothing coming back is its own news, so it gets the weight instead.
        for pid in added:
            lines.append(f"{MARKER_ADD} {_player_line(ctx, pid, bold=True)}")
        for pid in dropped:
            lines.append(f"{MARKER_DROP} {_player_line(ctx, pid, bold=not added)}")

        blocks.append("\n".join(lines))

    embed = discord.Embed(
        description="\n".join([_heading(title), "\n\n".join(blocks)]), color=color
    )

    if solo is not None:
        # Omit icon_url entirely when there is no avatar rather than passing a
        # placeholder — discord.py stringifies its MISSING sentinel into the
        # payload, which would reach Discord as a literal "..." image url.
        avatar = ctx.team_avatar(solo)
        if avatar:
            embed.set_author(name=ctx.team_name(solo), icon_url=avatar)
        else:
            embed.set_author(name=ctx.team_name(solo))

    # A headshot only reads as "this is who the alert is about" when there is
    # exactly one candidate; on a multi-player move it would just be arbitrary.
    focus = _focus_player(adds, drops)
    if focus:
        image = ctx.player_image(focus)
        if image:
            embed.set_thumbnail(url=image)

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
        incoming.setdefault(roster_id, []).append(
            _player_line(ctx, player_id, bold=True)
        )

    for pick in picks:
        receiver = pick.get("owner_id")
        origin = pick.get("roster_id")
        # "2027 1st" is how dynasty managers actually write it; "round pick" is
        # filler that pushes the line onto two rows in a narrow column.
        label = f"{MARKER_PICK} {pick.get('season')} {_ordinal(pick.get('round'))}"
        # Whose pick it originally was is the detail leagues argue about, and it
        # can be a third team that is not even part of this trade.
        if origin is not None and origin != receiver:
            label += f" · {ctx.team_name(origin)}"
        incoming.setdefault(receiver, []).append(label)

    # In a two-team trade the sender is necessarily the other side, so naming
    # them adds nothing — and worse, it reads like the pick annotation above,
    # where the name means "whose pick", not "who sent it".
    name_the_sender = len(txn.get("roster_ids") or []) > 2

    for entry in budget:
        receiver = entry.get("receiver")
        label = f"{MARKER_FAAB} ${entry.get('amount')} FAAB"
        if name_the_sender:
            label += f" · from {ctx.team_name(entry.get('sender'))}"
        incoming.setdefault(receiver, []).append(label)

    if not incoming:
        return None

    # Fall back to roster_ids so a team that only gave things up still appears.
    for roster_id in txn.get("roster_ids") or []:
        incoming.setdefault(roster_id, ["*Nothing*"])

    embed = discord.Embed(description=_heading("🔁 Trade"), color=COLOR_TRADE)
    rosters = sorted(incoming)

    # Discord fits three inline fields to a row, so a two-team trade reads as
    # two columns with a separator between them. Three still fits as three
    # columns; beyond that the columns get too narrow to read and stacking
    # full-width rows is clearer.
    columns = len(rosters) <= MAX_TRADE_COLUMNS

    for index, roster_id in enumerate(rosters):
        if columns and len(rosters) == 2 and index == 1:
            embed.add_field(name=TRADE_ARROW, value=ZERO_WIDTH, inline=True)

        body = "\n".join([COLUMN_RULE, *incoming[roster_id]])
        embed.add_field(
            name=ctx.team_name(roster_id),
            value=_truncate(body),
            inline=columns,
        )
    return embed


# -- helpers --------------------------------------------------------------


def _heading(label: str) -> str:
    """The event type as an oversized header with a rule under it."""
    return f"## {label}\n{DIVIDER}"


def _rosters_involved(adds: dict, drops: dict) -> list[int]:
    return sorted({*adds.values(), *drops.values()})


def _player_line(ctx: LeagueContext, player_id: str, *, bold: bool) -> str:
    """`**Name** · POS – TEAM`, with the name weighted by how much it matters."""
    name, detail = ctx.player_parts(player_id)
    name = f"**{name}**" if bold else name
    return f"{name} · {detail}" if detail else name


def _focus_player(adds: dict, drops: dict) -> str | None:
    """The one player an alert is really about, if there is one."""
    if len(adds) == 1:
        return next(iter(adds))
    if not adds and len(drops) == 1:
        return next(iter(drops))
    return None


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
