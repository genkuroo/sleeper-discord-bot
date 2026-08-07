"""The Discord client: one background poller plus the slash-command tree."""

from __future__ import annotations

import logging
import os
import time

import aiohttp
import discord
from discord.ext import tasks

from . import commands as slash_commands
from .config import Config
from .league import load_context
from .poller import dedupe, select_new, weeks_to_scan
from .render import render_transaction
from .sleeper import SleeperClient
from .store import Store

log = logging.getLogger(__name__)

# Discord accepts at most 10 embeds in one message.
EMBEDS_PER_MESSAGE = 10
PRUNE_INTERVAL = 24 * 60 * 60


class SleeperBot(discord.Client):
    def __init__(self, config: Config) -> None:
        # Least privilege: `guilds` is non-privileged and only exists so the
        # target channel is in cache. The bot never reads message content, so it
        # needs no privileged intent and no verification from Discord.
        super().__init__(intents=discord.Intents(guilds=True))

        self.config = config
        self.tree = discord.app_commands.CommandTree(self)
        self.store = Store(os.path.join(config.data_dir, "sleeper.db"))
        self.session: aiohttp.ClientSession | None = None
        self.sleeper: SleeperClient | None = None
        self._last_prune = 0.0

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": "sleeper-discord-bot (self-hosted)"}
        )
        self.sleeper = SleeperClient(
            self.session, self.config.league_id, self.config.data_dir
        )

        slash_commands.register(self)

        # Guild-scoped sync lands immediately; a global sync can take an hour to
        # propagate, which makes iterating on commands miserable.
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Synced slash commands to guild %s", self.config.guild_id)

        self.poll_transactions.change_interval(seconds=self.config.poll_seconds)
        self.poll_transactions.start()

    async def on_ready(self) -> None:
        log.info("Connected as %s", self.user)

    async def close(self) -> None:
        self.poll_transactions.cancel()
        if self.session and not self.session.closed:
            await self.session.close()
        self.store.close()
        await super().close()

    # -- the poller --------------------------------------------------------

    @tasks.loop(seconds=300)
    async def poll_transactions(self) -> None:
        # Every failure is caught here rather than escaping to the task runner:
        # a Sleeper outage or a transient DNS failure on the Pi should skip one
        # cycle, not take the bot down until someone notices.
        try:
            await self._poll_once()
        except Exception:
            log.exception("Poll cycle failed; will retry next interval")

    @poll_transactions.before_loop
    async def _before_poll(self) -> None:
        await self.wait_until_ready()

    async def _poll_once(self) -> None:
        assert self.sleeper is not None

        state = await self.sleeper.state()
        weeks = weeks_to_scan(state)

        raw: list[dict] = []
        for week in weeks:
            raw.extend(await self.sleeper.transactions(week))
        transactions = dedupe(raw)

        if not self.store.is_initialized():
            self._absorb_backlog(transactions)
            return

        by_id = {t["transaction_id"]: t for t in transactions if t.get("transaction_id")}
        unseen_ids = self.store.unseen(by_id)
        known = set(by_id) - unseen_ids

        new = select_new(
            transactions,
            known,
            alert_failed_waivers=self.config.alert_failed_waivers,
            max_backfill_hours=self.config.max_backfill_hours,
        )
        new_ids = {t["transaction_id"] for t in new}

        # Record the ones we looked at and deliberately skipped — a suppressed
        # failed claim, or something too old to be worth posting — so they are
        # not re-evaluated every cycle for the rest of the week. Anything still
        # "processing" is left alone: it has not happened yet, and the same id
        # comes back as "complete" when waivers run.
        settled = [
            txn_id
            for txn_id in unseen_ids
            if txn_id not in new_ids
            and by_id[txn_id].get("status") in {"complete", "failed"}
        ]
        if settled:
            self.store.mark_seen(settled)

        if new:
            await self._announce(new)

        self._maybe_prune()
        self._heartbeat()

    def _heartbeat(self) -> None:
        """Touch a file after every healthy cycle.

        The bot serves no HTTP, so there is no endpoint to probe. The age of
        this file is the only honest signal that the poller is still running —
        a container whose gateway connection has silently wedged still looks
        "up" to Docker otherwise.
        """
        path = os.path.join(self.config.data_dir, "heartbeat")
        try:
            with open(path, "w") as handle:
                handle.write(str(int(time.time())))
        except OSError as exc:
            log.warning("Could not write the heartbeat file: %s", exc)

    def _absorb_backlog(self, transactions: list[dict]) -> None:
        """On the very first run, record history without announcing it.

        A brand-new bot pointed at a league mid-season would otherwise open by
        replaying three weeks of waiver wire into the channel.
        """
        ids = [t["transaction_id"] for t in transactions if t.get("transaction_id")]
        self.store.mark_seen(ids)
        self.store.mark_initialized()
        log.info(
            "First run: absorbed %d existing transactions without posting. "
            "Alerts start with the next new move.",
            len(ids),
        )

    async def _announce(self, transactions: list[dict]) -> None:
        assert self.sleeper is not None

        channel = self.get_channel(self.config.channel_id)
        if channel is None:
            # Not in cache — a thread, or a channel created since connect.
            channel = await self.fetch_channel(self.config.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.error("Channel %s cannot receive messages", self.config.channel_id)
            return

        ctx = await load_context(self.sleeper)
        league_name = (await self.sleeper.league()).get("name", "Sleeper League")

        pending: list[tuple[str, discord.Embed]] = []
        unrenderable: list[str] = []
        for txn in transactions:
            embed = render_transaction(txn, ctx, league_name)
            if embed is None:
                unrenderable.append(txn["transaction_id"])
            else:
                pending.append((txn["transaction_id"], embed))

        if unrenderable:
            self.store.mark_seen(unrenderable)

        # Mark each batch seen only after Discord has accepted it. If the send
        # fails, the transaction stays unseen and the next cycle retries it.
        for start in range(0, len(pending), EMBEDS_PER_MESSAGE):
            batch = pending[start : start + EMBEDS_PER_MESSAGE]
            try:
                await channel.send(embeds=[embed for _, embed in batch])
            except discord.Forbidden:
                log.error(
                    "Missing permissions to post in channel %s. The bot needs "
                    "View Channel, Send Messages and Embed Links.",
                    self.config.channel_id,
                )
                return
            except discord.HTTPException as exc:
                log.error("Discord rejected a batch of %d embeds: %s", len(batch), exc)
                return

            self.store.mark_seen([txn_id for txn_id, _ in batch])
            log.info("Announced %d transaction(s)", len(batch))

    def _maybe_prune(self) -> None:
        if time.monotonic() - self._last_prune > PRUNE_INTERVAL:
            self.store.prune()
            self._last_prune = time.monotonic()
