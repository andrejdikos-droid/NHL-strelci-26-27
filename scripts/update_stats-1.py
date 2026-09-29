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


def main():
    league = load(LEAGUE_PATH, {})
    previous = load(STATS_PATH, {"players": {}})
    history = load(HISTORY_PATH, {"snapshots": []})

    season = league["season"]
    game_type = league.get("gameTypeId", 2)

    rows = fetch_stats(season, game_type)

    by_name = {
        norm(row.get("skaterFullName")): row
        for row in rows
        if row.get("skaterFullName")
    }

    by_last = {}
    for row in rows:
        full_name = row.get("skaterFullName", "")
        last_name = row.get("lastName") or (full_name.split()[-1] if full_name else "")
        by_last.setdefault(norm(last_name), []).append(row)

    roster_names = []
    for manager in league["managers"]:
        for slot in manager["roster"]:
            if slot["player"] not in roster_names:
                roster_names.append(slot["player"])

    players = {}
    unmatched = []

    for name in roster_names:
        match = by_name.get(norm(name))

        if not match:
            last = norm(name).split()[-1] if norm(name) else ""
            candidates = by_last.get(last, [])
            if len(candidates) == 1:
                match = candidates[0]

        old = previous.get("players", {}).get(name, {})
        old_goals = int(old.get("goals", 0) or 0)

        if match:
            goals = int(match.get("goals", 0) or 0)

            players[name] = {
                "playerId": match.get("playerId"),
                "goals": goals,
                "gamesPlayed": int(match.get("gamesPlayed", 0) or 0),
                "team": match.get("teamAbbrevs") or "",
                "shots": int(match.get("shots", 0) or 0),
                "shootingPct": match.get("shootingPct"),
                "deltaGoals": max(0, goals - old_goals),
                "found": True
            }
        else:
            players[name] = {
                "playerId": None,
                "goals": old_goals,
                "gamesPlayed": int(old.get("gamesPlayed", 0) or 0),
                "team": old.get("team", ""),
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

    team_totals = {}

    for manager in league["managers"]:
        goals = 0

        for slot in manager["roster"]:
            current = players.get(slot["player"], {}).get("goals", 0)

            goals += (
                int(slot.get("bankedGoals", 0))
                + max(
                    0,
                    current - int(slot.get("goalsAtAcquisition", 0))
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
