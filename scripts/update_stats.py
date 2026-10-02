#!/usr/bin/env python3
import json
import re
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
LEAGUE_PATH = ROOT / "data" / "league.json"
STATS_PATH = ROOT / "data" / "stats.json"
HISTORY_PATH = ROOT / "data" / "history.json"

# Hráči s menovcami v NHL: pri nich používame presné NHL player ID.
PLAYER_ID_OVERRIDES = {
    "Jason Robertson": 8480027,  # DAL
    "Jack Hughes": 8481559,      # NJD
    "Will Smith": 8484227,       # SJS
    "Sebastian Aho": 8478427,    # CAR
}

NAME_ALIASES = {
    "John-Jason Peterka": ["JJ Peterka", "J.J. Peterka"],
}

TEAM_FALLBACKS = {
    "Jason Robertson": "DAL",
    "Jack Hughes": "NJD",
    "Will Smith": "SJS",
    "Sebastian Aho": "CAR",
}


def load(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("’", "'").replace(".", "")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def fetch_stats(season, game_type):
    base = "https://api.nhle.com/stats/rest/en/skater/summary"
    params = {
        "isAggregate": "false",
        "isGame": "false",
        "start": "0",
        "limit": "-1",
        "sort": json.dumps(
            [{"property": "goals", "direction": "DESC"}],
            separators=(",", ":")
        ),
        "cayenneExp": f"seasonId={season} and gameTypeId={game_type}"
    }

    req = urllib.request.Request(
        base + "?" + urllib.parse.urlencode(params),
        headers={
            "User-Agent": "NHL-Strelci-26-27/1.0",
            "Accept": "application/json"
        }
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response).get("data", [])


def find_player(rows, name):
    forced_id = PLAYER_ID_OVERRIDES.get(name)

    if forced_id is not None:
        for row in rows:
            if int(row.get("playerId") or 0) == forced_id:
                return row
        return None

    target_norms = {norm(name)}
    target_norms.update(norm(x) for x in NAME_ALIASES.get(name, []))

    exact = [
        row for row in rows
        if norm(row.get("skaterFullName")) in target_norms
    ]

    # Ak existujú dvaja hráči s rovnakým menom, nikdy nevyberaj náhodne.
    if len(exact) == 1:
        return exact[0]

    return None


def fix_known_history_errors(history, n, value_per_goal):
    # Nick Robertson (PIT) bol omylom pripísaný Jasonovi Robertsonovi (DAL).
    # Oprava je idempotentná: zmení len presne známe chybné hodnoty.
    known = {
        "2026-10-01": (3, 1),
        "2026-10-02": (4, 2),
    }

    for snap in history.get("snapshots", []):
        date = snap.get("date")
        if date not in known:
            continue

        wrong, correct = known[date]
        managers = snap.get("managers", {})
        zelkis = managers.get("ZELKIS")

        if not zelkis or int(zelkis.get("goals", 0)) != wrong:
            continue

        zelkis["goals"] = correct
        league_total = sum(int(v.get("goals", 0)) for v in managers.values())

        for data in managers.values():
            goals = int(data.get("goals", 0))
            data["net"] = (n * goals - league_total) * value_per_goal


def main():
    league = load(LEAGUE_PATH, {})
    previous = load(STATS_PATH, {"players": {}})
    history = load(HISTORY_PATH, {"snapshots": []})

    season = league["season"]
    game_type = league.get("gameTypeId", 2)

    rows = fetch_stats(season, game_type)

    roster_names = []
    for manager in league["managers"]:
        for slot in manager["roster"]:
            if slot["player"] not in roster_names:
                roster_names.append(slot["player"])

    players = {}
    unmatched = []

    for name in roster_names:
        match = find_player(rows, name)

        old = previous.get("players", {}).get(name, {})
        old_goals = int(old.get("goals", 0) or 0)

        forced_id = PLAYER_ID_OVERRIDES.get(name)

        # Ak boli historicky uložené dáta menovca s iným ID, zahodíme ich.
        if forced_id is not None:
            old_player_id = int(old.get("playerId") or 0)
            if old_player_id not in (0, forced_id):
                old = {}
                old_goals = 0

        if match:
            goals = int(match.get("goals", 0) or 0)

            players[name] = {
                "playerId": match.get("playerId"),
                "goals": goals,
                "gamesPlayed": int(match.get("gamesPlayed", 0) or 0),
                "team": match.get("teamAbbrevs") or TEAM_FALLBACKS.get(name, ""),
                "shots": int(match.get("shots", 0) or 0),
                "shootingPct": match.get("shootingPct"),
                "deltaGoals": max(0, goals - old_goals),
                "found": True
            }
        else:
            players[name] = {
                "playerId": forced_id or old.get("playerId"),
                "goals": old_goals,
                "gamesPlayed": int(old.get("gamesPlayed", 0) or 0),
                "team": TEAM_FALLBACKS.get(name, old.get("team", "")),
                "shots": old.get("shots", 0),
                "shootingPct": old.get("shootingPct"),
                "deltaGoals": 0,
                "found": False
            }
            unmatched.append(name)

    now = datetime.now(timezone.utc).isoformat()

    stats_out = {
        "season": season,
        "gameTypeId": game_type,
        "updatedAt": now,
        "source": "NHL Stats API",
        "players": players,
        "unmatchedPlayers": unmatched,
        "message": "OK" if rows else "NHL API zatiaľ nevrátilo regular-season dáta."
    }

    STATS_PATH.write_text(
        json.dumps(stats_out, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    n = len(league["managers"])
    value_per_goal = league.get("valuePerGoal", 1)

    # Oprav staré snapshoty pred zapísaním dnešného.
    fix_known_history_errors(history, n, value_per_goal)

    team_totals = {}

    for manager in league["managers"]:
        goals = 0

        for slot in manager["roster"]:
            current = int(players.get(slot["player"], {}).get("goals", 0) or 0)

            goals += (
                int(slot.get("bankedGoals", 0) or 0)
                + max(
                    0,
                    current - int(slot.get("goalsAtAcquisition", 0) or 0)
                )
            )

        team_totals[manager["name"]] = goals

    league_total = sum(team_totals.values())

    snapshot = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "updatedAt": now,
        "managers": {
            name: {
                "goals": goals,
                "net": (n * goals - league_total) * value_per_goal
            }
            for name, goals in team_totals.items()
        }
    }

    snapshots = history.setdefault("snapshots", [])

    if snapshots and snapshots[-1].get("date") == snapshot["date"]:
        snapshots[-1] = snapshot
    else:
        snapshots.append(snapshot)

    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(
        f"Updated {len(players)} drafted players; "
        f"NHL rows={len(rows)}; unmatched={len(unmatched)}"
    )


if __name__ == "__main__":
    main()
