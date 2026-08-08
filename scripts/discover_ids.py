#!/usr/bin/env python3
"""List the servers and channels the bot can see, with their ids.

Finding a guild or channel id normally means enabling Developer Mode and
right-clicking around, and a copied id gives no hint about whether the bot can
actually post there. The bot already knows both, so ask it.

Needs only DISCORD_TOKEN — run it before the other two ids are filled in:

    python scripts/discover_ids.py

Channels are marked by whether the bot can post: a channel it can see but not
send to is the single most common reason a first deploy goes silent.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from sleeperbot.config import load_dotenv  # noqa: E402

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"

REQUIRED_PERMISSIONS = ["view_channel", "send_messages", "embed_links"]


class DiscoveryClient(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents(guilds=True))
        self.found_any = False

    async def on_ready(self) -> None:
        try:
            self._report()
        finally:
            await self.close()

    def _report(self) -> None:
        print(f"\nConnected as {BOLD}{self.user}{RESET}")

        if not self.guilds:
            print(
                f"\n{RED}The bot is not in any server.{RESET}\n\n"
                "Open the install link from the Developer Portal's Installation\n"
                "tab and add it to your test server, then run this again.\n"
            )
            return

        for guild in self.guilds:
            self.found_any = True
            print(f"\n{BOLD}{guild.name}{RESET}")
            print(f"  DISCORD_GUILD_ID={guild.id}\n")

            postable = []
            for channel in guild.text_channels:
                permissions = channel.permissions_for(guild.me)
                missing = [
                    p for p in REQUIRED_PERMISSIONS if not getattr(permissions, p)
                ]
                if missing:
                    print(f"  {RED}✗{RESET} #{channel.name:<24} {DIM}{channel.id}  "
                          f"missing: {', '.join(m.replace('_', ' ') for m in missing)}{RESET}")
                else:
                    postable.append(channel)
                    print(f"  {GREEN}✓{RESET} #{channel.name:<24} {DIM}{channel.id}{RESET}")

            if postable:
                print(f"\n  {DIM}Pick one and put it in .env, e.g.:{RESET}")
                print(f"  DISCORD_CHANNEL_ID={postable[0].id}   {DIM}# #{postable[0].name}{RESET}")
            else:
                print(f"\n  {RED}The bot cannot post in any channel here.{RESET}")
                print(f"  {DIM}Give its role View Channel, Send Messages and "
                      f"Embed Links.{RESET}")
        print()


def main() -> int:
    load_dotenv()
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        print(
            "DISCORD_TOKEN is not set. Put it in .env (see .env.example) or pass "
            "it inline:\n\n    DISCORD_TOKEN=... python scripts/discover_ids.py\n",
            file=sys.stderr,
        )
        return 2

    client = DiscoveryClient()
    try:
        client.run(token, log_handler=None)
    except discord.LoginFailure:
        print(
            f"\n{RED}Discord rejected the token.{RESET}\n\n"
            "It comes from the Developer Portal's Bot tab (Reset Token), not the\n"
            "Application ID on General Information. A token is a long string with\n"
            "dots in it; the Application ID is just digits.\n",
            file=sys.stderr,
        )
        return 1

    return 0 if client.found_any else 1


if __name__ == "__main__":
    raise SystemExit(main())
