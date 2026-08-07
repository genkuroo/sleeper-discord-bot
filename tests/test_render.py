"""Tests for turning a transaction into something a human wants to read."""

from __future__ import annotations

import pytest

from sleeperbot.league import LeagueContext
from sleeperbot.render import render_transaction
from tests import fixtures


@pytest.fixture
def ctx():
    return LeagueContext(
        rosters=fixtures.ROSTERS, users=fixtures.USERS, players=fixtures.PLAYERS
    )


def fields_for(embed, name: str) -> str:
    return next(str(f.value) for f in embed.fields if f.name == name)


def _text(embed) -> str:
    """Flatten an embed so a test can assert on everything it displays."""
    parts = [embed.title or "", embed.description or ""]
    for field in embed.fields:
        parts.extend([field.name or "", str(field.value or "")])
    return "\n".join(parts)


# -- names ----------------------------------------------------------------


def test_custom_team_name_wins_over_username(ctx):
    assert ctx.team_name(1) == "Hurts Donut"


def test_manager_without_a_team_name_falls_back_to_username(ctx):
    assert ctx.team_name(3) == "marcus99"


def test_orphan_roster_still_gets_a_name(ctx):
    """A manager quitting mid-season leaves owner_id null — don't crash on it."""
    assert ctx.team_name(4) == "Roster 4"


def test_unknown_roster_id_does_not_raise(ctx):
    assert ctx.team_name(99) == "Roster 99"


def test_unknown_player_id_renders_as_a_placeholder(ctx):
    """A player signed hours ago can beat our once-a-day catalog refresh."""
    assert ctx.player_name("999999") == "Player 999999"


def test_team_defense_renders(ctx):
    assert ctx.player_name("SF") == "San Francisco 49ers (DEF – SF)"


# -- roster moves ---------------------------------------------------------


def test_free_agent_add_shows_both_sides(ctx):
    embed = render_transaction(fixtures.FREE_AGENT_ADD, ctx, "Test League")
    text = _text(embed)

    assert "Free Agent Pickup" in embed.title
    assert "Hurts Donut" in text
    assert "Bijan Robinson (RB – ATL)" in text
    assert "San Francisco 49ers (DEF – SF)" in text


def test_waiver_claim_shows_the_faab_bid(ctx):
    embed = render_transaction(fixtures.WAIVER_CLAIM, ctx, "Test League")
    text = _text(embed)

    assert "Waiver Claim" in embed.title
    assert "$27" in text
    assert "Kyler Murray" in text


def test_priority_waiver_renders_without_a_faab_field(ctx):
    """Leagues on waiver priority send no waiver_bid — don't invent a "$0" bid."""
    embed = render_transaction(fixtures.PRIORITY_WAIVER, ctx, "Test League")
    text = _text(embed)

    assert "Waiver Claim" in embed.title
    assert "Travis Kelce" in text
    assert "Patrick Mahomes" in text
    assert "FAAB" not in text
    assert "$" not in text


def test_failed_claim_explains_why(ctx):
    embed = render_transaction(fixtures.FAILED_WAIVER, ctx, "Test League")
    text = _text(embed)

    assert "Failed" in embed.title
    assert "already on another roster" in text


def test_drop_only_move_is_not_labelled_a_pickup(ctx):
    embed = render_transaction(fixtures.DROP_ONLY, ctx, "Test League")
    assert "Drop" in embed.title
    assert "Pickup" not in embed.title


# -- trades ---------------------------------------------------------------


def test_trade_lists_what_each_team_receives(ctx):
    embed = render_transaction(fixtures.TRADE, ctx, "Test League")
    fields = {field.name: str(field.value) for field in embed.fields}

    assert "Hurts Donut receives" in fields
    assert "Kupp Noodles receives" in fields
    assert "Christian McCaffrey" in fields["Hurts Donut receives"]
    assert "Justin Jefferson" in fields["Kupp Noodles receives"]


def test_trade_names_the_original_owner_of_a_third_party_pick(ctx):
    """"2027 1st (from marcus99)" is the detail leagues actually argue about."""
    embed = render_transaction(fixtures.TRADE, ctx, "Test League")
    received = next(
        str(f.value) for f in embed.fields if f.name == "Hurts Donut receives"
    )
    assert "2027 1st round pick (from marcus99)" in received


def test_trade_annotates_a_pick_with_whose_pick_it_is(ctx):
    """Roster 1's own 2027 3rd going to roster 2 is "from Hurts Donut"."""
    embed = render_transaction(fixtures.TRADE, ctx, "Test League")
    received = next(
        str(f.value) for f in embed.fields if f.name == "Kupp Noodles receives"
    )
    pick_line = next(line for line in received.splitlines() if "round pick" in line)
    assert pick_line == "• 2027 3rd round pick (from Hurts Donut)"


def test_buying_back_your_own_pick_is_not_annotated(ctx):
    """"2027 2nd (from Hurts Donut)" would be noise on Hurts Donut's own line."""
    buyback = dict(fixtures.TRADE)
    buyback["draft_picks"] = [
        {"season": "2027", "round": 2, "roster_id": 1, "previous_owner_id": 2, "owner_id": 1}
    ]
    embed = render_transaction(buyback, ctx, "Test League")
    received = next(
        str(f.value) for f in embed.fields if f.name == "Hurts Donut receives"
    )
    pick_line = next(line for line in received.splitlines() if "round pick" in line)
    assert pick_line == "• 2027 2nd round pick"


def test_picks_only_trade_renders_with_no_players(ctx):
    """Dynasty leagues trade pure pick packages; `adds` is empty on those."""
    embed = render_transaction(fixtures.PICKS_ONLY_TRADE, ctx, "Test League")
    fields = {field.name: str(field.value) for field in embed.fields}

    assert "Hurts Donut receives" in fields
    assert "marcus99 receives" in fields
    assert "2027 1st round pick" in fields["Hurts Donut receives"]
    assert "2028 2nd round pick" in fields["Hurts Donut receives"]
    assert "2027 2nd round pick" in fields["marcus99 receives"]


def test_pick_ordinals_past_tenth_are_correct(ctx):
    """A 23-round startup means 11th/12th/13th ordinals actually come up."""
    embed = render_transaction(fixtures.PICKS_ONLY_TRADE, ctx, "Test League")
    received = fields_for(embed, "marcus99 receives")
    assert "2027 11th round pick" in received
    assert "11st" not in received and "11nd" not in received


def test_trade_includes_faab(ctx):
    embed = render_transaction(fixtures.TRADE, ctx, "Test League")
    received = next(
        str(f.value) for f in embed.fields if f.name == "Kupp Noodles receives"
    )
    assert "$15 FAAB (from Hurts Donut)" in received


# -- embed limits ---------------------------------------------------------


def test_large_trade_stays_inside_discord_field_limits(ctx):
    """Discord rejects the whole message if any field value exceeds 1024."""
    huge = dict(fixtures.TRADE)
    huge["adds"] = {str(pid): 1 for pid in range(1, 400)}
    embed = render_transaction(huge, ctx, "Test League")

    for field in embed.fields:
        assert len(str(field.value)) <= 1024


def test_unknown_transaction_type_is_skipped(ctx):
    assert render_transaction({"type": "something_new"}, ctx, "Test League") is None
