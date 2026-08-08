"""Tests for turning a transaction into something a human wants to read."""

from __future__ import annotations

import pytest

from sleeperbot.league import LeagueContext
from sleeperbot.render import render_transaction
from sleeperbot import samples as fixtures


@pytest.fixture
def ctx():
    return LeagueContext(
        rosters=fixtures.ROSTERS, users=fixtures.USERS, players=fixtures.PLAYERS
    )


def fields_for(embed, name: str) -> str:
    return next(str(f.value) for f in embed.fields if f.name == name)


def heading(embed) -> str:
    """The event type, which lives in the description as a `##` header."""
    return (embed.description or "").splitlines()[0].removeprefix("## ").strip()


def body(embed) -> str:
    """Everything below the heading and its divider rule."""
    lines = (embed.description or "").splitlines()
    return "\n".join(lines[2:])


def _text(embed) -> str:
    """Flatten an embed so a test can assert on everything it displays."""
    parts = [embed.author.name or "", embed.description or ""]
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

    assert "Free Agent Pickup" in heading(embed)
    assert "Hurts Donut" in text
    assert "Bijan Robinson" in text
    assert "San Francisco 49ers" in text


def test_a_drawn_divider_separates_the_heading_from_the_players(ctx):
    """Discord has no horizontal-rule markdown, so the rule is box characters."""
    embed = render_transaction(fixtures.FREE_AGENT_ADD, ctx, "Test League")
    lines = embed.description.splitlines()

    assert lines[0].startswith("## ")
    assert set(lines[1]) == {"━"}
    assert body(embed).startswith("🟢")


def test_trades_get_the_same_heading_treatment(ctx):
    """Every alert should read as the same family, whatever its shape."""
    embed = render_transaction(fixtures.TRADE, ctx, "Test League")
    lines = embed.description.splitlines()

    assert lines[0] == "## 🔁 Trade"
    assert set(lines[1]) == {"━"}


def test_add_and_drop_get_coloured_markers(ctx):
    """Embeds cannot colour text, so the marker emoji carry the colour."""
    embed = render_transaction(fixtures.FREE_AGENT_ADD, ctx, "Test League")
    lines = embed.description.splitlines()

    added = next(line for line in lines if "Bijan Robinson" in line)
    dropped = next(line for line in lines if "San Francisco" in line)

    assert added.startswith("🟢")
    assert dropped.startswith("🔴")


def test_the_added_player_is_the_bold_one(ctx):
    """Weight follows importance: the pickup is the news, the drop is context."""
    embed = render_transaction(fixtures.FREE_AGENT_ADD, ctx, "Test League")
    lines = embed.description.splitlines()

    assert "**Bijan Robinson**" in next(l for l in lines if "Bijan" in l)
    assert "**San Francisco" not in next(l for l in lines if "San Francisco" in l)


def test_a_drop_with_nothing_added_is_bolded_instead(ctx):
    """With no pickup to headline, the drop itself is the news."""
    embed = render_transaction(fixtures.DROP_ONLY, ctx, "Test League")
    assert "**Travis Kelce**" in embed.description


def test_team_goes_in_the_author_line_with_its_avatar(ctx):
    """One roster involved, so the team gets the author slot to itself."""
    embed = render_transaction(fixtures.FREE_AGENT_ADD, ctx, "Test League")

    assert embed.author.name == "Hurts Donut"
    assert embed.author.icon_url.startswith("https://sleepercdn.com/avatars/")
    # The team is in the author line now, so repeating it inline is noise.
    assert "Hurts Donut" not in embed.description


def test_a_league_specific_avatar_beats_the_account_one(ctx):
    """Setting one for this league specifically means they meant it here."""
    from sleeperbot.league import _avatar_url

    user = {"avatar": "account-hash", "metadata": {"avatar": "league-hash"}}
    assert _avatar_url(user).endswith("league-hash")


def test_a_manager_with_no_avatar_gets_a_plain_author_line(ctx):
    """Most managers never set one — that must not render a broken image."""
    embed = render_transaction(fixtures.DROP_ONLY, ctx, "Test League")

    assert embed.author.name == "marcus99"
    # Must be genuinely absent. Passing discord.py's MISSING sentinel here
    # serialises to the string "..." and reaches Discord as a broken image url.
    assert embed.author.icon_url is None


def test_a_multi_roster_move_falls_back_to_inline_headers(ctx):
    """The author line holds one name, so several teams have to go inline."""
    multi = dict(fixtures.FREE_AGENT_ADD, adds={"9509": 1}, drops={"1466": 3})
    embed = render_transaction(multi, ctx, "Test League")

    assert embed.author.name is None
    assert "### Hurts Donut" in embed.description
    assert "### marcus99" in embed.description


def test_single_add_gets_the_players_headshot(ctx):
    embed = render_transaction(fixtures.WAIVER_CLAIM, ctx, "Test League")
    assert embed.thumbnail.url == "https://sleepercdn.com/content/nfl/players/5849.jpg"


def test_a_defense_uses_the_team_logo_not_a_headshot(ctx):
    """Team defenses have no headshot; their player_id is the team code."""
    drop_def = dict(fixtures.DROP_ONLY, adds=None, drops={"SF": 1})
    embed = render_transaction(drop_def, ctx, "Test League")
    assert embed.thumbnail.url == "https://sleepercdn.com/images/team_logos/nfl/sf.png"


def test_a_multi_player_move_gets_no_thumbnail(ctx):
    """With two players added, either headshot would be an arbitrary pick."""
    multi = dict(fixtures.FREE_AGENT_ADD, adds={"9509": 1, "1466": 1}, drops=None)
    embed = render_transaction(multi, ctx, "Test League")
    assert embed.thumbnail.url is None


def test_waiver_claim_shows_the_faab_bid(ctx):
    embed = render_transaction(fixtures.WAIVER_CLAIM, ctx, "Test League")
    text = _text(embed)

    assert "Waiver Claim" in heading(embed)
    assert "$27" in text
    assert "Kyler Murray" in text


def test_priority_waiver_renders_without_a_faab_field(ctx):
    """Leagues on waiver priority send no waiver_bid — don't invent a "$0" bid."""
    embed = render_transaction(fixtures.PRIORITY_WAIVER, ctx, "Test League")
    text = _text(embed)

    assert "Waiver Claim" in heading(embed)
    assert "Travis Kelce" in text
    assert "Patrick Mahomes" in text
    assert "FAAB" not in text
    assert "$" not in text


def test_failed_claim_explains_why(ctx):
    embed = render_transaction(fixtures.FAILED_WAIVER, ctx, "Test League")
    text = _text(embed)

    assert "Failed" in heading(embed)
    assert "already on another roster" in text


def test_drop_only_move_is_not_labelled_a_pickup(ctx):
    embed = render_transaction(fixtures.DROP_ONLY, ctx, "Test League")
    assert "Drop" in heading(embed)
    assert "Pickup" not in heading(embed)


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
