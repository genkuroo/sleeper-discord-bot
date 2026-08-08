#!/usr/bin/env python3
"""Connect once, check the setup, post sample alerts, exit.

The bot itself is silent by design — it posts only when the league does
something. That makes a first deploy hard to judge: an empty channel looks
identical whether the bot is working perfectly or holding a bad channel id.

This connects with the same config the bot uses, reports what it can actually
see and do, optionally posts the sample transactions from the test fixtures,
and exits. Point it at a test server first.

    python scripts/smoke_post.py            # check only, posts nothing
    python scripts/smoke_post.py --post     # also post the sample alerts

It never opens the SQLite store, so nothing here can mark a real transaction
as already-announced.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from sleeperbot.config import Config, ConfigError  # noqa: E402
from sleeperbot.league import LeagueContext  # noqa: E402
from sleeperbot.render import render_transaction  # noqa: E402

OK = "\033[32m✓\033[0m"
BAD = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"

# The three permissions the bot cannot work without. Discord silently drops a
# send rather than telling the channel why, so checking up front is the
# difference between a clear error and a mystery.
REQUIRED_PERMISSIONS = ["view_channel", "send_messages", "embed_links"]


class SmokeClient(discord.Client):
    def __init__(self, config: Config, post: bool) -> None:
        super().__init__(intents=discord.Intents(guilds=True))
        self.config = config
        self.post = post
        self.failures: list[str] = []

    async def on_ready(self) -> None:
        try:
            await self._check()
        except Exception as exc:  # noqa: BLE001 - report anything, then exit
            self.failures.append(f"unexpected error: {exc}")
        finally:
            await self.close()

    async def _check(self) -> None:
        print(f"\n{OK} Connected as \033[1m{self.user}\033[0m")

        guild = self.get_guild(self.config.guild_id)
        if guild is None:
            self.failures.append(
                f"Not a member of guild {self.config.guild_id}. Either the id is "
                "wrong or the bot was never invited to that server."
            )
            print(f"{BAD} Guild {self.config.guild_id} not found")
            return
        print(f"{OK} Guild: \033[1m{guild.name}\033[0m ({guild.id})")

        channel = guild.get_channel(self.config.channel_id)
        if channel is None:
            self.failures.append(
                f"Channel {self.config.channel_id} is not in {guild.name}. A channel "
                "id copied from a different server is the usual cause."
            )
            print(f"{BAD} Channel {self.config.channel_id} not found in this guild")
            return
        print(f"{OK} Channel: \033[1m#{channel.name}\033[0m ({channel.id})")

        permissions = channel.permissions_for(guild.me)
        missing = [p for p in REQUIRED_PERMISSIONS if not getattr(permissions, p)]
        for name in REQUIRED_PERMISSIONS:
            mark = OK if getattr(permissions, name) else BAD
            print(f"{mark} Permission: {name.replace('_', ' ')}")
        if missing:
            self.failures.append(
                "Missing permissions in that channel: " + ", ".join(missing)
            )
            return

        # Slash commands are registered by the bot's own startup, not here.
        # Report what Discord currently has so a stale set is visible.
        try:
            registered = await self.tree.fetch_commands(guild=discord.Object(guild.id))
            names = sorted(c.name for c in registered)
            if names:
                print(f"{OK} Slash commands registered: {', '.join(names)}")
            else:
                print(f"{WARN} No slash commands registered yet "
                      "(the bot registers them when it starts)")
        except discord.HTTPException as exc:
            print(f"{WARN} Could not read slash commands: {exc}")

        if not self.post:
            print(f"\n{WARN} Nothing posted. Re-run with --post to send samples.\n")
            return

        await self._post_samples(channel)

    async def _post_samples(self, channel) -> None:
        from sleeperbot import samples as fixtures

        ctx = LeagueContext(
            rosters=fixtures.ROSTERS,
            users=fixtures.USERS,
            players=fixtures.PLAYERS,
        )
        samples = [
            fixtures.FREE_AGENT_ADD,
            fixtures.PRIORITY_WAIVER,   # matches Money Hole: no FAAB figure
            fixtures.TRADE,
            fixtures.PICKS_ONLY_TRADE,  # dynasty pick package
        ]
        embeds = [
            embed
            for embed in (render_transaction(t, ctx, "SAMPLE — not a real league")
                          for t in samples)
            if embed is not None
        ]

        try:
            await channel.send(
                content="**Smoke test** — sample data, nothing below actually happened.",
                embeds=embeds,
            )
        except discord.Forbidden:
            self.failures.append("Discord refused the send despite the permission check.")
            print(f"{BAD} Forbidden when posting")
            return
        except discord.HTTPException as exc:
            self.failures.append(f"Discord rejected the message: {exc}")
            print(f"{BAD} Post rejected: {exc}")
            return

        print(f"{OK} Posted {len(embeds)} sample alerts to #{channel.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--post", action="store_true", help="actually send the sample alerts"
    )
    args = parser.parse_args()

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    client = SmokeClient(config, post=args.post)
    try:
        client.run(config.discord_token, log_handler=None)
    except discord.LoginFailure:
        print(f"\n{BAD} Discord rejected the token. Reset it in the Developer "
              "Portal and copy the whole string.\n", file=sys.stderr)
        return 1

    if client.failures:
        print(f"\n{BAD} \033[1mSetup is not ready:\033[0m", file=sys.stderr)
        for failure in client.failures:
            print(f"    - {failure}", file=sys.stderr)
        print(file=sys.stderr)
        return 1

    print(f"\n{OK} \033[1mReady to deploy.\033[0m\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
