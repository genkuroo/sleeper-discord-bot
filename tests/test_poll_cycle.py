"""End-to-end poll cycles against a stubbed Sleeper API and a fake channel.

The two behaviours worth protecting are the ones the league would notice:
the bot must not open by replaying history, and it must never post the same
transaction twice no matter how often it polls or restarts.
"""

from __future__ import annotations

import discord
import pytest

from sleeperbot.bot import SleeperBot
from sleeperbot.config import Config
from sleeperbot import samples as fixtures


class FakeSleeper:
    """Stands in for SleeperClient with the same async surface."""

    def __init__(self, transactions: list[dict], week: int = 9) -> None:
        self._transactions = transactions
        self._week = week
        self.transaction_calls: list[int] = []

    async def state(self) -> dict:
        return {"week": self._week, "season": "2026"}

    async def league(self) -> dict:
        return {"name": "Test League", "season": "2026"}

    async def rosters(self) -> list[dict]:
        return fixtures.ROSTERS

    async def users(self) -> list[dict]:
        return fixtures.USERS

    async def players(self) -> dict:
        return fixtures.PLAYERS

    async def transactions(self, week: int) -> list[dict]:
        self.transaction_calls.append(week)
        # Every scanned week returns the same set, which is also the cheapest
        # way to prove the overlapping-week dedupe works.
        return list(self._transactions)


class FakeChannel(discord.abc.Messageable):
    """Captures sends instead of hitting Discord.

    Subclasses Messageable for real rather than faking the isinstance check,
    so the guard in _announce is exercised as written.
    """

    def __init__(self) -> None:
        self.messages: list[list[discord.Embed]] = []

    async def _get_channel(self):  # required by the Messageable contract
        return self

    async def send(self, embeds=None, **kwargs):
        self.messages.append(list(embeds or []))

    @property
    def embeds(self) -> list[discord.Embed]:
        return [embed for message in self.messages for embed in message]

    @property
    def titles(self) -> list[str]:
        return [embed.title for embed in self.embeds]


@pytest.fixture
def make_bot(monkeypatch, tmp_path):
    created = []

    def _make(transactions, **overrides):
        monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
        monkeypatch.setenv("DISCORD_GUILD_ID", "123")
        monkeypatch.setenv("DISCORD_CHANNEL_ID", "456")
        monkeypatch.setenv("SLEEPER_LEAGUE_ID", "999")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        for key, value in overrides.items():
            monkeypatch.setenv(key, value)

        bot = SleeperBot(Config.from_env())
        bot.sleeper = FakeSleeper(transactions)
        channel = FakeChannel()
        monkeypatch.setattr(SleeperBot, "get_channel", lambda self, _id: channel)
        created.append(bot)
        return bot, channel

    yield _make
    for bot in created:
        bot.store.close()


# -- first run ------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_run_absorbs_history_without_posting(make_bot):
    """A bot pointed at a live league must not replay three weeks into chat."""
    bot, channel = make_bot(fixtures.ALL_TRANSACTIONS)

    await bot._poll_once()

    assert channel.messages == []
    assert bot.store.is_initialized() is True


@pytest.mark.asyncio
async def test_transactions_after_the_first_run_do_post(make_bot):
    bot, channel = make_bot(fixtures.ALL_TRANSACTIONS)
    await bot._poll_once()  # absorbs the backlog

    new_trade = dict(fixtures.TRADE, transaction_id="t-trade-2")
    bot.sleeper._transactions = fixtures.ALL_TRANSACTIONS + [new_trade]
    await bot._poll_once()

    assert channel.titles == ["🔁 Trade"]


# -- deduplication --------------------------------------------------------


@pytest.mark.asyncio
async def test_polling_repeatedly_posts_each_transaction_once(make_bot):
    bot, channel = make_bot([])
    await bot._poll_once()  # initialise on an empty league

    bot.sleeper._transactions = fixtures.ALL_TRANSACTIONS
    for _ in range(5):
        await bot._poll_once()

    # Four announceable moves; the pending, failed and stale ones are filtered.
    assert len(channel.embeds) == 4
    assert sorted(channel.titles) == sorted(
        ["🆓 Free Agent Pickup", "💰 Waiver Claim", "🔁 Trade", "✂️ Drop"]
    )


@pytest.mark.asyncio
async def test_overlapping_week_scan_does_not_double_post(make_bot):
    """Weeks 8, 9 and 10 all return the same rows in this stub."""
    bot, channel = make_bot([])
    await bot._poll_once()

    bot.sleeper._transactions = [fixtures.TRADE]
    await bot._poll_once()

    assert bot.sleeper.transaction_calls[-3:] == [8, 9, 10]
    assert len(channel.embeds) == 1


@pytest.mark.asyncio
async def test_a_restart_does_not_replay(make_bot):
    """The SQLite store is what makes `docker compose up` safe to run twice."""
    bot, channel = make_bot([])
    await bot._poll_once()

    bot.sleeper._transactions = [fixtures.TRADE]
    await bot._poll_once()
    assert len(channel.embeds) == 1

    # Same data directory, fresh process.
    restarted, new_channel = make_bot([fixtures.TRADE])
    await restarted._poll_once()
    assert new_channel.messages == []


# -- ordering and filtering ----------------------------------------------


@pytest.mark.asyncio
async def test_a_pending_waiver_posts_only_once_it_completes(make_bot):
    """The claim must not leak before waivers run, but must land after."""
    bot, channel = make_bot([])
    await bot._poll_once()

    bot.sleeper._transactions = [fixtures.PENDING_WAIVER]
    await bot._poll_once()
    assert channel.messages == []

    completed = dict(fixtures.PENDING_WAIVER, status="complete")
    bot.sleeper._transactions = [completed]
    await bot._poll_once()
    assert channel.titles == ["💰 Waiver Claim"]


@pytest.mark.asyncio
async def test_failed_waivers_appear_when_enabled(make_bot):
    bot, channel = make_bot([], ALERT_FAILED_WAIVERS="1")
    await bot._poll_once()

    bot.sleeper._transactions = [fixtures.FAILED_WAIVER]
    await bot._poll_once()

    assert channel.titles == ["❌ Failed Waiver Claim"]


@pytest.mark.asyncio
async def test_a_batch_posts_in_chronological_order(make_bot):
    bot, channel = make_bot([])
    await bot._poll_once()

    bot.sleeper._transactions = fixtures.ALL_TRANSACTIONS
    await bot._poll_once()

    stamps = [embed.timestamp for embed in channel.embeds]
    assert stamps == sorted(stamps)


@pytest.mark.asyncio
async def test_a_healthy_cycle_writes_a_heartbeat(make_bot, tmp_path):
    bot, _ = make_bot([])
    await bot._poll_once()
    await bot._poll_once()
    assert (tmp_path / "heartbeat").exists()
