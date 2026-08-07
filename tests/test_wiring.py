"""Checks the bot assembles correctly without connecting to Discord.

These catch the class of mistake that only shows up at runtime otherwise — a
command that never got registered, a task loop that isn't bound to the instance
— which on a headless Pi means noticing days later that nothing ever posted.
"""

from __future__ import annotations

import pytest

from sleeperbot import commands as slash_commands
from sleeperbot.bot import SleeperBot
from sleeperbot.config import Config, ConfigError
from sleeperbot.store import Store

EXPECTED_COMMANDS = ["matchup", "roster", "standings", "trades"]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("DISCORD_GUILD_ID", "123")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "456")
    monkeypatch.setenv("SLEEPER_LEAGUE_ID", "999")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("POLL_SECONDS", "60")
    return tmp_path


@pytest.fixture
def bot(env):
    instance = SleeperBot(Config.from_env())
    yield instance
    instance.store.close()


# -- config ---------------------------------------------------------------


def test_missing_required_setting_fails_fast(monkeypatch, tmp_path):
    """Better to refuse to start than to crash-loop under restart: unless-stopped."""
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_GUILD_ID", "123")
    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        Config.from_env()


def test_non_numeric_id_is_rejected_with_a_useful_message(env, monkeypatch):
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "#league-chat")
    with pytest.raises(ConfigError, match="DISCORD_CHANNEL_ID must be a number"):
        Config.from_env()


def test_defaults_are_applied(env, monkeypatch):
    monkeypatch.delenv("POLL_SECONDS", raising=False)
    config = Config.from_env()
    assert config.poll_seconds == 300
    assert config.max_backfill_hours == 48
    assert config.alert_failed_waivers is False


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("yes", True),
                                            ("0", False), ("false", False), ("", False)])
def test_failed_waiver_flag_parsing(env, monkeypatch, value, expected):
    monkeypatch.setenv("ALERT_FAILED_WAIVERS", value)
    assert Config.from_env().alert_failed_waivers is expected


# -- bot assembly ---------------------------------------------------------


def test_every_slash_command_registers(bot):
    slash_commands.register(bot)
    assert sorted(c.name for c in bot.tree.get_commands()) == EXPECTED_COMMANDS


def test_roster_command_has_team_autocomplete(bot):
    """Without this, players have to type the team name exactly."""
    slash_commands.register(bot)
    roster = next(c for c in bot.tree.get_commands() if c.name == "roster")
    assert roster._params["team"].autocomplete is not None


def test_poll_loop_takes_the_configured_interval(bot):
    bot.poll_transactions.change_interval(seconds=bot.config.poll_seconds)
    assert bot.poll_transactions.seconds == 60


def test_poll_loop_waits_for_ready(bot):
    """Posting before the guild cache is warm means get_channel returns None."""
    assert bot.poll_transactions._before_loop is not None


def test_bot_requests_no_privileged_intents(bot):
    """message_content and members require Discord approval — we need neither."""
    assert bot.intents.guilds is True
    assert bot.intents.message_content is False
    assert bot.intents.members is False
    assert bot.intents.presences is False


# -- store ----------------------------------------------------------------


def test_store_reports_unseen_ids_only(tmp_path):
    store = Store(str(tmp_path / "test.db"))
    store.mark_seen(["a", "b"])
    assert store.unseen(["a", "b", "c"]) == {"c"}
    store.close()


def test_store_survives_a_restart(tmp_path):
    """This file is the only thing stopping a replay on every deploy."""
    path = str(tmp_path / "test.db")

    first = Store(path)
    first.mark_seen(["t-1"])
    first.mark_initialized()
    first.close()

    second = Store(path)
    assert second.is_initialized() is True
    assert second.unseen(["t-1", "t-2"]) == {"t-2"}
    second.close()


def test_marking_the_same_id_twice_is_harmless(tmp_path):
    store = Store(str(tmp_path / "test.db"))
    store.mark_seen(["a"])
    store.mark_seen(["a"])
    assert store.unseen(["a"]) == set()
    store.close()
