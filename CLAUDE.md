# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

A Discord bot that announces Sleeper fantasy football transactions in a league's
Discord channel, plus a few read-only slash commands. Deployed as one container
in the `homelab-pi` stack (build context `../sleeper-discord-bot`).

## Invariants — don't break these

- **Never announce a transaction twice.** The SQLite store in `DATA_DIR` is the
  only thing preventing a replay on restart. Any change to the poll cycle must
  keep "mark seen only after Discord accepts the send" true, or a failed send
  silently swallows a transaction.
- **Never announce a `processing` transaction.** Pending waiver claims leak
  bids before waivers run. Only `complete` posts; `failed` is opt-in.
- **The first run posts nothing.** `_absorb_backlog` is load-bearing — without
  it, a fresh deploy replays weeks of history into the channel.
- **Don't fetch `/players/nfl` more than once a day.** It is ~5 MB and Sleeper
  asks callers not to. The disk cache in `SleeperClient.players()` enforces it.
- **No privileged intents.** The bot uses `Intents(guilds=True)` only. Adding
  `message_content` or `members` would require Discord verification and let the
  bot read league chat, which it has no reason to do.
- **No secrets in committed files.** `DISCORD_TOKEN` lives in `.env` on the Pi;
  `.env.example` documents keys with empty values.

## Layout

| File | Role |
|---|---|
| `sleeperbot/sleeper.py` | Async Sleeper API client and all caching |
| `sleeperbot/poller.py` | Pure rules: which weeks, what counts as new |
| `sleeperbot/render.py` | Transaction dict → Discord embed |
| `sleeperbot/league.py` | id → team name and player name lookups |
| `sleeperbot/store.py` | SQLite record of announced transaction ids |
| `sleeperbot/bot.py` | Discord client, poll loop, announcing |
| `sleeperbot/commands.py` | Slash commands |

`poller.py` and `render.py` are deliberately free of Discord and network calls
so the interesting rules stay testable as plain functions.

## Sleeper API notes

- Fully public, no auth. Base `https://api.sleeper.app/v1`.
- Transactions are indexed by **week**, and a move can land in a neighbouring
  week's bucket around the Wednesday waiver run — hence the three-week scan
  window in `weeks_to_scan`. Week 0 is the preseason bucket.
- Everything is ids. `adds`/`drops` map `player_id → roster_id`; resolving a
  roster to a name needs both `/rosters` and `/users`.
- A draft pick carries three roster ids: `roster_id` (whose pick it originally
  is), `previous_owner_id`, and `owner_id` (who has it now).
- Scores split into `fpts` and `fpts_decimal` (hundredths).
- Sleeper returns `200` with a `null` body for empty resources, not `404`.

## Testing changes

```bash
pytest                                  # 57 tests, no network
python scripts/preview.py               # render sample alerts to the terminal
python scripts/preview.py --league <id> # render a real league, read-only
```

Any new transaction shape needs a fixture in `tests/fixtures.py` matching the
real payload's field names, plus a render test asserting on the embed text.

Discord limits worth remembering: 10 embeds per message, 25 fields per embed,
1024 characters per field value, 25 autocomplete choices. Exceeding any of them
rejects the whole message.
