"""Entry point: `python -m sleeperbot`."""

from __future__ import annotations

import logging
import sys

from .bot import SleeperBot
from .config import Config, ConfigError


def main() -> int:
    try:
        config = Config.from_env()
    except ConfigError as exc:
        # Fail loudly and immediately rather than connecting and misbehaving.
        # Under `restart: unless-stopped` a misconfigured container would
        # otherwise crash-loop with a less obvious error.
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    bot = SleeperBot(config)
    # run() installs its own signal handlers and closes the loop cleanly on
    # SIGTERM, which is what `docker compose down` sends.
    bot.run(config.discord_token, log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
