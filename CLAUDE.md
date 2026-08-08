# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

A Discord bot that announces Sleeper fantasy football transactions in a league's
Discord channel, plus a few read-only slash commands. Deployed as one container
in the `homelab-pi` stack (build context `../sleeper-discord-bot`).

## The governing rule — never show what Sleeper doesn't

**The bot mirrors what a league member can already see in the Sleeper app. If
Sleeper hides it from other managers, this bot must not publish it to a channel
they are all reading.**

Critically, **the public API is not the boundary.** It is unauthenticated and
returns things the app deliberately withholds, so "the endpoint returned it" is
never justification for displaying it. Check what a manager sees in Sleeper.

Worked examples:

- **Winning waiver bid** — in Sleeper's transaction log, so it is shown.
- **Failed waiver claims and their bid amounts** — Sleeper shows a failed claim
  only to the manager who made it. The API returns everyone's. This is why
  `ALERT_FAILED_WAIVERS` defaults off: switching it on publishes losing bids and
  leaks how managers value players. Treat it as a leak, not a noise setting.
- **Pending (`processing`) claims** — not visible to anyone until waivers run,
  and posting one would spoil a bid before it resolves.

Before adding any new field to an alert, ask where a manager sees it in the app.
If the answer is "they don't", do not add it.

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
- `settings.waiver_bid` on a transaction only exists in FAAB leagues
  (`waiver_type: 2`). On waiver priority (`waiver_type: 0`) it is absent even
  though the league still carries a default `waiver_budget` — so the bid field
  must stay conditional rather than defaulting to `$0`.
- Sleeper returns `200` with a `null` body for empty resources, not `404`.
- **A league's id changes every season.** Sleeper creates a new `league_id` on
  rollover and links back via `previous_league_id`. Anything pinned to one id —
  which is this whole bot — silently stops seeing activity when that happens.
- A traded pick carries no slot number. `{season, round, roster_id}` only. The
  slot comes from inverting `slot_to_roster_id` on that season's draft object,
  and is meaningless until `draft_order` is populated — before that it is an
  identity placeholder, not a real order. A future season's draft does not
  exist yet at all, and when it does it hangs off the next league id.

## Testing changes

```bash
pytest                                  # 60 tests, no network
python scripts/preview.py               # render sample alerts to the terminal
python scripts/preview.py --league <id> # render a real league, read-only
```

Any new transaction shape needs a fixture in `tests/fixtures.py` matching the
real payload's field names, plus a render test asserting on the embed text.

Discord limits worth remembering: 10 embeds per message, 25 fields per embed,
1024 characters per field value, 25 autocomplete choices. Exceeding any of them
rejects the whole message.

## Discord rendering, verified by posting rather than assumed

- **Markdown headers (`#`/`##`/`###`) work in an embed *description* only.**
  They are ignored in field names and field values, which render the literal
  hashes. This was tested live — do not "tidy up" a field by adding one.
- A `##` header in the description renders *larger* than the embed's own
  `title`, which is why the event type lives there and `title` is unused.
- **There is no horizontal-rule markdown.** `---` does not become a line, so
  dividers are drawn with box characters (`━`, `▬`).
- **Embeds cannot colour arbitrary text.** Only an ANSI code block can, and it
  renders grey and monospaced on mobile. Coloured emoji are the portable way.
- **Only four image slots exist**, none per-field: author icon, thumbnail (top
  right), image (bottom, full width), footer icon. Per-team images need
  Components V2 sections (accessory renders on the right) or custom emoji.
- **Inline fields collapse to full width on narrow screens**, so column layouts
  degrade to stacked lists on phones.
- Passing `discord.utils.MISSING` for `icon_url` serialises the string `"..."`
  rather than omitting the field. Omit the argument instead.
