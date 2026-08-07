"""Slash commands.

Every one of these defers its reply first. Discord kills an interaction that
goes unanswered for three seconds, and a cold command can need a league fetch, a
roster fetch and a player-catalog load before it has anything to say.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from .league import load_context
from .poller import dedupe, weeks_to_scan
from .render import COLOR_TRADE, render_transaction

log = logging.getLogger(__name__)

POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]


def register(bot) -> None:
    """Attach every command to the bot's tree. Called once, from setup_hook."""

    @bot.tree.command(name="standings", description="Current league standings")
    async def standings(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        ctx = await load_context(bot.sleeper)
        league = await bot.sleeper.league()

        rows = []
        for roster in ctx.rosters:
            settings = roster.get("settings") or {}
            rows.append(
                {
                    "team": ctx.team_name(roster.get("roster_id")),
                    "wins": settings.get("wins", 0),
                    "losses": settings.get("losses", 0),
                    "ties": settings.get("ties", 0),
                    "points": _points(settings, "fpts"),
                    "against": _points(settings, "fpts_against"),
                }
            )

        # Points break the tie, which is how Sleeper's default settings do it.
        rows.sort(key=lambda r: (r["wins"], r["points"]), reverse=True)

        lines = []
        for rank, row in enumerate(rows, start=1):
            record = f"{row['wins']}-{row['losses']}"
            if row["ties"]:
                record += f"-{row['ties']}"
            lines.append(
                f"{rank:>2}. {_pad(row['team'], 18)} {record:>7}  "
                f"{row['points']:>7.1f} PF  {row['against']:>7.1f} PA"
            )

        embed = discord.Embed(
            title=f"🏈 {league.get('name', 'League')} — Standings",
            description="```\n" + "\n".join(lines) + "\n```",
            color=COLOR_TRADE,
        )
        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="roster", description="Show a team's roster")
    @app_commands.describe(team="Which team (leave blank for a list)")
    async def roster(interaction: discord.Interaction, team: str) -> None:
        await interaction.response.defer()
        ctx = await load_context(bot.sleeper)

        roster_id = ctx.roster_id_for_team(team)
        if roster_id is None:
            await interaction.followup.send(
                f"No team called **{team}**. Teams: {', '.join(ctx.team_choices)}",
                ephemeral=True,
            )
            return

        data = ctx.roster(roster_id) or {}
        starters = [p for p in (data.get("starters") or []) if p and p != "0"]
        all_players = [p for p in (data.get("players") or []) if p]
        bench = [p for p in all_players if p not in set(starters)]

        embed = discord.Embed(title=f"📋 {ctx.team_name(roster_id)}", color=COLOR_TRADE)
        if starters:
            embed.add_field(
                name="Starters",
                value=_player_list(ctx, starters),
                inline=False,
            )
        if bench:
            embed.add_field(
                name="Bench",
                value=_player_list(ctx, _sort_by_position(ctx, bench)),
                inline=False,
            )
        if not starters and not bench:
            embed.description = "*Empty roster.*"

        await interaction.followup.send(embed=embed)

    @roster.autocomplete("team")
    async def team_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            ctx = await load_context(bot.sleeper)
        except Exception:  # autocomplete must never surface an error to the user
            return []
        matches = [t for t in ctx.team_choices if current.lower() in t.lower()]
        # Discord caps a choice list at 25 and rejects the whole response if
        # you exceed it, so slice rather than trusting league size.
        return [app_commands.Choice(name=t, value=t) for t in matches[:25]]

    @bot.tree.command(name="matchup", description="This week's matchups and scores")
    async def matchup(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        state = await bot.sleeper.state()
        week = state.get("week") or 1
        ctx = await load_context(bot.sleeper)
        entries = await bot.sleeper.matchups(week)

        if not entries:
            await interaction.followup.send(f"No matchups posted for week {week} yet.")
            return

        pairings: dict[int, list[dict]] = {}
        for entry in entries:
            pairings.setdefault(entry.get("matchup_id"), []).append(entry)

        embed = discord.Embed(title=f"⚔️ Week {week} Matchups", color=COLOR_TRADE)
        for matchup_id in sorted(k for k in pairings if k is not None):
            sides = pairings[matchup_id]
            names = [
                f"{ctx.team_name(s.get('roster_id'))} — {s.get('points') or 0:.1f}"
                for s in sides
            ]
            embed.add_field(name=f"Matchup {matchup_id}", value="\n".join(names), inline=True)

        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="trades", description="Recent completed trades")
    @app_commands.describe(count="How many to show (1-5, default 3)")
    async def trades(interaction: discord.Interaction, count: int = 3) -> None:
        await interaction.response.defer()
        count = max(1, min(count, 5))

        state = await bot.sleeper.state()
        found: list[dict] = []
        for week in weeks_to_scan(state):
            found.extend(await bot.sleeper.transactions(week))

        completed = [
            t
            for t in dedupe(found)
            if t.get("type") == "trade" and t.get("status") == "complete"
        ]
        completed.sort(key=lambda t: t.get("status_updated") or 0, reverse=True)

        if not completed:
            await interaction.followup.send("No trades in the last few weeks.")
            return

        ctx = await load_context(bot.sleeper)
        league = await bot.sleeper.league()
        embeds = [
            embed
            for embed in (
                render_transaction(t, ctx, league.get("name", "League"))
                for t in completed[:count]
            )
            if embed is not None
        ]
        await interaction.followup.send(embeds=embeds)


# -- helpers --------------------------------------------------------------


def _points(settings: dict, key: str) -> float:
    """Sleeper splits scores into whole and hundredths fields."""
    whole = settings.get(key) or 0
    decimal = settings.get(f"{key}_decimal") or 0
    return whole + decimal / 100


def _pad(text: str, width: int) -> str:
    if len(text) > width:
        return text[: width - 1] + "…"
    return text.ljust(width)


def _player_list(ctx, player_ids: list[str]) -> str:
    lines = [f"• {ctx.player_name(pid)}" for pid in player_ids]
    text = "\n".join(lines)
    return text[:1023] + "…" if len(text) > 1024 else text


def _sort_by_position(ctx, player_ids: list[str]) -> list[str]:
    def key(pid: str):
        position = ctx.players.get(str(pid), {}).get("position", "")
        rank = POSITION_ORDER.index(position) if position in POSITION_ORDER else len(POSITION_ORDER)
        return (rank, ctx.player_name(pid))

    return sorted(player_ids, key=key)
