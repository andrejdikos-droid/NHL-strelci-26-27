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

# NHL has a few active players with identical full names.
# For these drafted players we pin the exact NHL player ID so goals can never
# be attributed to the wrong namesake.
PLAYER_ID_OVERRIDES = {
    "Jason Robertson": 8480027,   # DAL
    "Jack Hughes": 8481559,       # NJD
    "Will Smith": 8484227,        # SJS
    "Sebastian Aho": 8478427,     # CAR
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

    targets = [name] + NAME_ALIASES.get(name, [])
    target_norms = {norm(x) for x in targets}

    exact = [
        row for row in rows
        if norm(row.get("skaterFullName")) in target_norms
    ]

    # Never choose arbitrarily when two active NHL players have the same name.
    if len(exact) == 1:
        return exact[0]

    return None


def fix_known_history_errors(history, n, value_per_goal):
    # One-time correction:
    # 2026-10-01 snapshot accidentally credited Nick Robertson's 2 PIT goals
    # to ZELKIS's Jason Robertson. Jason had not yet played for DAL.
    for snap in history.get("snapshots", []):
        if snap.get("date") != "2026-10-01":
            continue

        managers = snap.get("managers", {})
        zelkis = managers.get("ZELKIS")
        if not zelkis or int(zelkis.get("goals", 0)) != 3:
            continue

        zelkis["goals"] = 1
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
