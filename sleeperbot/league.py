"""Turns Sleeper's id-based payloads into human-readable names.

Sleeper transactions are expressed entirely in ids: ``{"adds": {"4046": 3}}``
means "roster 3 added player 4046". Making that legible needs two joins the API
does not do for you — roster_id to a team name (via the roster's owner) and
player_id to a name — so this module builds both lookups once per poll.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Sleeper serves these publicly, no key and no referer check, so an embed can
# point Discord straight at them.
PLAYER_HEADSHOT = "https://sleepercdn.com/content/nfl/players/{}.jpg"
TEAM_LOGO = "https://sleepercdn.com/images/team_logos/nfl/{}.png"
MANAGER_AVATAR = "https://sleepercdn.com/avatars/thumbs/{}"


@dataclass
class LeagueContext:
    rosters: list[dict] = field(default_factory=list)
    users: list[dict] = field(default_factory=list)
    players: dict[str, dict[str, str]] = field(default_factory=dict)

    _team_names: dict[int, str] = field(default_factory=dict, init=False)
    _team_avatars: dict[int, str | None] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        users_by_id = {user["user_id"]: user for user in self.users}

        for roster in self.rosters:
            roster_id = roster.get("roster_id")
            owner = users_by_id.get(roster.get("owner_id"))
            self._team_names[roster_id] = _display_name(owner, roster_id)
            self._team_avatars[roster_id] = _avatar_url(owner)

    def team_avatar(self, roster_id: int | None) -> str | None:
        """The manager's avatar image, or None if they never set one."""
        return self._team_avatars.get(roster_id)

    def team_name(self, roster_id: int | None) -> str:
        if roster_id is None:
            return "Unknown team"
        return self._team_names.get(roster_id, f"Roster {roster_id}")

    def player_parts(self, player_id: str) -> tuple[str, str]:
        """Split a player into (name, "POS – TEAM").

        Kept separate so a renderer can weight them differently — the name is
        the news, the position and team are context.
        """
        player = self.players.get(str(player_id))
        if not player:
            return f"Player {player_id}", ""

        position = player.get("position") or ""
        team = player.get("team") or "FA"
        detail = " – ".join(part for part in (position, team) if part)
        return player["name"], detail

    def player_image(self, player_id: str) -> str | None:
        """Sleeper's public CDN headshot, or the team logo for a defense.

        Team defenses have no headshot — their player_id *is* the team
        abbreviation, which is exactly what the logo path wants.
        """
        player = self.players.get(str(player_id))
        if not player:
            return None
        if player.get("position") == "DEF":
            return TEAM_LOGO.format(str(player_id).lower())
        return PLAYER_HEADSHOT.format(player_id)

    def player_name(self, player_id: str) -> str:
        """Render a player as ``Name (POS – TEAM)``.

        Unknown ids still render as the raw id rather than raising: a player
        added within minutes of signing can appear in a transaction before our
        once-a-day player cache knows about them, and a slightly ugly alert is
        much better than a missed one.
        """
        player = self.players.get(str(player_id))
        if not player:
            return f"Player {player_id}"

        name = player["name"]
        position = player.get("position") or ""
        team = player.get("team") or "FA"
        if position or team:
            return f"{name} ({position} – {team})".replace("( – ", "(")
        return name

    def roster(self, roster_id: int) -> dict | None:
        for roster in self.rosters:
            if roster.get("roster_id") == roster_id:
                return roster
        return None

    @property
    def team_choices(self) -> list[str]:
        return [self._team_names[rid] for rid in sorted(self._team_names)]

    def roster_id_for_team(self, name: str) -> int | None:
        for roster_id, team in self._team_names.items():
            if team.lower() == name.lower():
                return roster_id
        return None


def _display_name(user: dict | None, roster_id: int | None) -> str:
    """Prefer the custom team name, fall back to the Sleeper username.

    Both fallbacks are load-bearing: managers who never set a team name have no
    ``metadata.team_name``, and an orphan roster — a manager who left mid-season
    — has no owner record at all, leaving only the roster number to go on.
    """
    if not user:
        return f"Roster {roster_id}"
    metadata = user.get("metadata") or {}
    return metadata.get("team_name") or user.get("display_name") or f"Roster {roster_id}"


def _avatar_url(user: dict | None) -> str | None:
    """A manager's avatar, preferring one they set for this league specifically.

    Sleeper keeps two: an account-wide avatar, and an optional per-league one in
    `metadata.avatar`. Someone who bothered to set a league-specific one meant it
    for this league, so it wins. Plenty of managers have neither.
    """
    if not user:
        return None
    metadata = user.get("metadata") or {}
    avatar = metadata.get("avatar") or user.get("avatar")
    if not avatar:
        return None
    # A league avatar can be stored as a full URL rather than a bare hash.
    if str(avatar).startswith("http"):
        return avatar
    return MANAGER_AVATAR.format(avatar)


async def load_context(client) -> LeagueContext:
    """Fetch the three lookups an alert needs. All three are cached upstream."""
    rosters = await client.rosters()
    users = await client.users()
    players = await client.players()
    return LeagueContext(rosters=rosters, users=users, players=players)
