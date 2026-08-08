# sleeper-discord-bot

[![CI](https://github.com/genkuroo/sleeper-discord-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/genkuroo/sleeper-discord-bot/actions/workflows/ci.yml)

A Discord bot that posts your Sleeper fantasy football league's transactions
into your league's chat channel the moment they happen — waiver claims, free
agent pickups, drops, and trades broken down by who got what. In FAAB leagues
the winning bid is shown; leagues on waiver priority send no bid, and the alert
simply omits it.

It also answers slash commands, so nobody has to leave Discord to check
standings or a roster.

```
🔁 Trade
  Hurts Donut receives
    • Christian McCaffrey (RB – SF)
    • 2027 1st round pick (from marcus99)
  Kupp Noodles receives
    • Justin Jefferson (WR – MIN)
    • 2027 3rd round pick (from Hurts Donut)
    • $15 FAAB (from Hurts Donut)
```

See it for yourself before setting anything up:

```bash
python scripts/preview.py                    # bundled sample league
python scripts/preview.py --league <id>      # your real league, read-only
```

## The problem

Sleeper's own notifications are per-device and easy to miss, and its in-app
league chat is a ghost town because everyone already talks in Discord. So the
league finds out someone grabbed the handcuff RB when they open the app on
Sunday morning — too late to counter-bid, and with no thread to complain in.

The fix is to move the transaction feed to where the league already is.

## How it works

Sleeper's read API is fully public — no key, no OAuth, no account. That makes
the data side easy and moves all the interesting problems into *change
detection*, because there is no "what happened since?" endpoint. Every poll
returns the whole week and the bot has to work out what is new.

```
┌─────────────────────────────────────────────┐
│  sleeper-discord-bot (one container)        │
│                                             │
│  poll loop ──every 5 min──> Sleeper API     │
│      │                                      │
│      │  diff against SQLite of posted ids   │
│      v                                      │
│  new? ──> render embed ──> #league-chat     │
│                                             │
│  slash commands <──gateway──> Discord       │
└─────────────────────────────────────────────┘
```

Four decisions carry most of the weight:

**Deduplication is persisted, not in-memory.** Every announced `transaction_id`
goes into SQLite on a Docker volume. Without that, every `docker compose up`
would replay the week into the channel — the single most obvious way this bot
could embarrass itself.

**The first run posts nothing.** Pointing a fresh bot at a league mid-season
would otherwise dump three weeks of waiver wire into chat at once. On first
boot it records everything it can see as already-announced and starts alerting
from the next real move.

**Old news stays unposted.** `MAX_BACKFILL_HOURS` (default 48) means a Pi that
was unplugged overnight comes back and stays quiet about what the league
already knows, instead of narrating yesterday.

**Pending waivers are never announced.** A claim sitting in `processing` has not
happened yet, and posting it would leak someone's bid before waivers run. Only
`complete` transactions post — failed ones are available behind
`ALERT_FAILED_WAIVERS=1`, off by default because on a contested player every
losing bid is its own event and it buries the actual news.

### Caching

The bot's only heavy call is the player catalog — `/players/nfl` is about 5 MB
of every NFL player, and Sleeper explicitly asks callers to fetch it at most
once a day. It is trimmed to the three fields an alert renders (name, position,
team), written to disk, and reused for 24 hours. League and roster metadata get
short in-memory TTLs. Transactions are never cached, since they are the thing
being watched.

### Slash commands

| Command | Does |
|---|---|
| `/standings` | Records and points for/against, ranked |
| `/roster <team>` | A team's starters and bench (with autocomplete) |
| `/matchup` | This week's pairings and live scores |
| `/trades [count]` | The last few completed trades |

The bot requests **no privileged intents**. It never reads message content, so
it needs no approval from Discord and cannot see what your league is saying.

## Setup

### 1. Find your league id

```bash
python scripts/find_league_id.py <your-sleeper-username>
```

Or take it from the web URL: `sleeper.com/leagues/<THIS PART>/team`.

### 2. Create the Discord bot

1. <https://discord.com/developers/applications> → **New Application**
2. **Bot** → **Reset Token** → copy it (this is `DISCORD_TOKEN`)
3. **Installation** → set install link scopes to `bot` and
   `applications.commands`, with permissions **View Channel**, **Send
   Messages**, **Embed Links**
4. Open the generated link and add it to your server

Enable **Developer Mode** in Discord (Settings → Advanced), then right-click
your server for `DISCORD_GUILD_ID` and the target channel for
`DISCORD_CHANNEL_ID`.

### 3. Configure and run

```bash
cp .env.example .env      # fill in the four required values
pip install -r requirements.txt
python -m sleeperbot
```

### 4. Deploy to the Pi

This repo is one of the app repos orchestrated by
[`homelab-pi`](../homelab-pi), which builds it from `../sleeper-discord-bot`:

```bash
cd ~/Code/homelab-pi
docker compose up -d --build sleeper-bot
docker compose logs -f sleeper-bot
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | — | Bot token (required) |
| `DISCORD_GUILD_ID` | — | Server id (required) |
| `DISCORD_CHANNEL_ID` | — | Channel to post in (required) |
| `SLEEPER_LEAGUE_ID` | — | League to watch (required) |
| `POLL_SECONDS` | `300` | How often to check Sleeper |
| `MAX_BACKFILL_HOURS` | `48` | Never announce anything older than this |
| `ALERT_FAILED_WAIVERS` | `0` | Include failed waiver claims |
| `DATA_DIR` | `/data` | SQLite state and the player cache |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

Missing required configuration fails at startup with a named variable rather
than crash-looping under `restart: unless-stopped`.

## Operating it

The bot serves no HTTP, so there is nothing to curl. It writes a `heartbeat`
file after each healthy poll and the container's `HEALTHCHECK` watches its age
— a gateway connection that has silently wedged shows up as `unhealthy` instead
of looking fine forever.

```bash
docker compose ps sleeper-bot          # health status
docker compose logs -f sleeper-bot     # what it's doing
```

**To replay nothing after moving the bot:** keep the volume. **To force a clean
slate:** delete `sleeper.db` from the data volume — the next run re-absorbs
history silently and starts fresh.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

60 tests, no network and no Discord connection. They cover the announcement
rules (what posts, what stays quiet), the rendering of every transaction shape
including third-party draft picks and Discord's 1024-character field limit, and
full poll cycles against a stubbed API — including that a restart with an
existing database posts nothing.

## Limitations

- **Sleeper only.** ESPN and Yahoo need authenticated scraping; this needs none.
- **Polling, not push.** Sleeper has no webhooks, so alerts land within
  `POLL_SECONDS`.
- **One league per instance.** Run a second container for a second league.
- **Alerts are transactions only.** Matchup results and live draft picks are
  deliberately not implemented — `/matchup` covers scores on demand.
