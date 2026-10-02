#!/usr/bin/env python3
import json
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LEAGUE_PATH = ROOT / "data" / "league.json"
STATS_PATH = ROOT / "data" / "stats.json"
HISTORY_PATH = ROOT / "data" / "history.json"
LOCAL_TZ = ZoneInfo("Europe/Bratislava")


def load(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def get_json(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NHL-Strelci-26-27/3.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def fetch_stats(season, game_type):
    base = "https://api.nhle.com/stats/rest/en/skater/summary"
    params = {
        "isAggregate": "false",
        "isGame": "false",
        "start": "0",
        "limit": "-1",
        "sort": json.dumps([{"property": "goals", "direction": "DESC"}], separators=(",", ":")),
        "cayenneExp": f"seasonId={season} and gameTypeId={game_type}"
    }
    return get_json(base + "?" + urllib.parse.urlencode(params)).get("data", [])


def fetch_night_goal_counts(game_date, game_type):
    score = get_json(f"https://api-web.nhle.com/v1/score/{game_date}")
    counts = Counter()
    game_ids = []

    for game in score.get("games", []):
        if int(game.get("gameType") or 0) != int(game_type):
            continue
        game_id = game.get("id")
        if not game_id:
            continue

        game_ids.append(game_id)
        pbp = get_json(f"https://api-web.nhle.com/v1/gamecenter/{game_id}/play-by-play")

        for play in pbp.get("plays", []):
            if play.get("typeDescKey") != "goal":
                continue
            period_type = (play.get("periodDescriptor", {}).get("periodType") or "").upper()
            if period_type == "SO":
                continue
            player_id = play.get("details", {}).get("scoringPlayerId")
            if player_id:
                counts[int(player_id)] += 1

    return counts, game_ids


def validate_league(league):
    slots = [s for m in league.get("managers", []) for s in m.get("roster", [])]
    expected = len(league.get("managers", [])) * 8

    if len(slots) != expected:
        raise ValueError(f"Roster integrity error: expected {expected} slots, got {len(slots)}.")

    missing = [s.get("player") for s in slots if not s.get("playerId")]
    if missing:
        raise ValueError("Missing playerId for: " + ", ".join(missing))

    ids = [int(s["playerId"]) for s in slots]
    if len(ids) != len(set(ids)):
        duplicates = sorted({x for x in ids if ids.count(x) > 1})
        raise ValueError(f"Duplicate NHL playerId in league: {duplicates}")


def fix_known_history_errors(history, n, value_per_goal):
    known = {"2026-10-01": (3, 1), "2026-10-02": (4, 2)}

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

    validate_league(league)

    season = league["season"]
    game_type = league.get("gameTypeId", 2)
    rows = fetch_stats(season, game_type)

    by_id = {
        int(row["playerId"]): row
        for row in rows
        if row.get("playerId") is not None
    }

    local_now = datetime.now(LOCAL_TZ)
    night_game_date = (local_now.date() - timedelta(days=1)).isoformat()
    night_goal_counts, night_game_ids = fetch_night_goal_counts(night_game_date, game_type)

    players = {}
    unmatched = []

    for manager in league["managers"]:
        for slot in manager["roster"]:
            name = slot["player"]
            player_id = int(slot["playerId"])
            match = by_id.get(player_id)

            old = previous.get("players", {}).get(name, {})
            old_id = int(old.get("playerId") or 0)
            if old_id not in (0, player_id):
                old = {}

            old_goals = int(old.get("goals", 0) or 0)
            overnight_goals = int(night_goal_counts.get(player_id, 0))

            if match:
                goals = int(match.get("goals", 0) or 0)
                players[name] = {
                    "playerId": player_id,
                    "goals": goals,
                    "gamesPlayed": int(match.get("gamesPlayed", 0) or 0),
                    "team": match.get("teamAbbrevs") or old.get("team", ""),
                    "shots": int(match.get("shots", 0) or 0),
                    "shootingPct": match.get("shootingPct"),
                    "deltaGoals": overnight_goals,
                    "found": True
                }
            else:
                players[name] = {
                    "playerId": player_id,
                    "goals": old_goals,
                    "gamesPlayed": int(old.get("gamesPlayed", 0) or 0),
                    "team": old.get("team", ""),
                    "shots": old.get("shots", 0),
                    "shootingPct": old.get("shootingPct"),
                    "deltaGoals": overnight_goals,
                    "found": False
                }
                unmatched.append(name)

    now = datetime.now(timezone.utc).isoformat()

    stats_out = {
        "season": season,
        "gameTypeId": game_type,
        "updatedAt": now,
        "source": "NHL Stats API · playerId locked · nightly goals from play-by-play",
        "nightGameDate": night_game_date,
        "nightGameIds": night_game_ids,
        "players": players,
        "unmatchedPlayers": unmatched,
        "message": "OK" if rows else "NHL API zatiaľ nevrátilo regular-season dáta."
    }

    STATS_PATH.write_text(json.dumps(stats_out, ensure_ascii=False, indent=2), encoding="utf-8")

    n = len(league["managers"])
    value_per_goal = league.get("valuePerGoal", 1)
    fix_known_history_errors(history, n, value_per_goal)

    team_totals = {}
    for manager in league["managers"]:
        goals = 0
        for slot in manager["roster"]:
            current = int(players.get(slot["player"], {}).get("goals", 0) or 0)
            goals += int(slot.get("bankedGoals", 0) or 0) + max(
                0, current - int(slot.get("goalsAtAcquisition", 0) or 0)
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

    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    overnight_drafted_goals = sum(int(p.get("deltaGoals", 0) or 0) for p in players.values())

    print(
        f"ID-LOCK update: {len(players)} players; "
        f"NHL rows={len(rows)}; unmatched={len(unmatched)}; "
        f"nightDate={night_game_date}; nightGames={len(night_game_ids)}; "
        f"draftedNightGoals={overnight_drafted_goals}"
    )


if __name__ == "__main__":
    main()
