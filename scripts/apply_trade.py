#!/usr/bin/env python3
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
LEAGUE_PATH = ROOT / "data" / "league.json"
TRADES_PATH = ROOT / "data" / "trades.json"
EVENT_PATH = os.environ.get("GITHUB_EVENT_PATH")
ADMIN = "andrejdikos-droid"


def norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("’", "'").replace(".", "")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def load(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


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
    target = norm(name)

    exact = [
        row for row in rows
        if norm(row.get("skaterFullName")) == target
    ]
    if len(exact) == 1:
        return exact[0]

    last = target.split()[-1] if target else ""
    candidates = []
    for row in rows:
        full = row.get("skaterFullName", "")
        row_last = row.get("lastName") or (full.split()[-1] if full else "")
        if norm(row_last) == last:
            candidates.append(row)

    if len(candidates) == 1:
        return candidates[0]

    return None


def parse_body(body):
    fields = {}
    for raw in (body or "").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        fields[key.strip().upper()] = value.strip()

    required = ["MANAGER", "OUT", "IN", "COUNT_TRADE"]
    missing = [k for k in required if not fields.get(k)]
    if missing:
        raise ValueError(f"Missing fields: {', '.join(missing)}")

    return fields


def main():
    if not EVENT_PATH:
        raise RuntimeError("GITHUB_EVENT_PATH missing")

    event = json.loads(Path(EVENT_PATH).read_text(encoding="utf-8"))
    issue = event.get("issue", {})
    actor = issue.get("user", {}).get("login", "")

    if actor != ADMIN:
        raise PermissionError(f"Only {ADMIN} may register trades.")

    title = issue.get("title", "")
    if not title.startswith("[TRADE]"):
        raise ValueError("Issue is not a trade request.")

    fields = parse_body(issue.get("body", ""))

    manager_name = fields["MANAGER"]
    out_player = fields["OUT"]
    in_player = fields["IN"]
    counted_trade = fields["COUNT_TRADE"].lower() == "true"
    note = fields.get("NOTE", "")
    if note == "-":
        note = ""

    if norm(out_player) == norm(in_player):
        raise ValueError("OUT and IN player cannot be the same.")

    league = load(LEAGUE_PATH, {})
    trades = load(TRADES_PATH, {"records": []})

    manager = next(
        (m for m in league.get("managers", []) if m.get("name") == manager_name),
        None
    )
    if not manager:
        raise ValueError(f"Unknown manager: {manager_name}")

    slot = next(
        (s for s in manager.get("roster", []) if norm(s.get("player")) == norm(out_player)),
        None
    )
    if not slot:
        raise ValueError(f"{out_player} is not on {manager_name}'s current roster.")

    for other_manager in league.get("managers", []):
        for other_slot in other_manager.get("roster", []):
            if norm(other_slot.get("player")) == norm(in_player):
                raise ValueError(
                    f"{in_player} is already rostered by {other_manager.get('name')}."
                )

    if counted_trade and int(manager.get("tradesUsed", 0)) >= int(
        league.get("rules", {}).get("maxTradesPerManager", 4)
    ):
        raise ValueError(f"{manager_name} has already used all 4 trades.")

    rows = fetch_stats(
        league["season"],
        league.get("gameTypeId", 2)
    )

    out_row = find_player(rows, out_player)
    in_row = find_player(rows, in_player)

    current_out_goals = int(out_row.get("goals", 0) or 0) if out_row else 0
    current_in_goals = int(in_row.get("goals", 0) or 0) if in_row else 0

    previous_banked = int(slot.get("bankedGoals", 0) or 0)
    acquired_at = int(slot.get("goalsAtAcquisition", 0) or 0)

    credited_out_goals = (
        previous_banked
        + max(0, current_out_goals - acquired_at)
    )

    slot["player"] = in_player
    slot["bankedGoals"] = credited_out_goals
    slot["goalsAtAcquisition"] = current_in_goals

    trade_number = None
    if counted_trade:
        manager["tradesUsed"] = int(manager.get("tradesUsed", 0)) + 1
        trade_number = manager["tradesUsed"]

    now = datetime.now(timezone.utc)

    trades.setdefault("records", []).append({
        "date": now.date().isoformat(),
        "timestamp": now.isoformat(),
        "manager": manager_name,
        "outPlayer": out_player,
        "inPlayer": in_player,
        "countedTrade": counted_trade,
        "tradeNumber": trade_number,
        "bankedGoals": credited_out_goals,
        "incomingGoalsAtAcquisition": current_in_goals,
        "type": "regular_trade" if counted_trade else "special_replacement",
        "note": note,
        "githubIssue": issue.get("number")
    })

    LEAGUE_PATH.write_text(
        json.dumps(league, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    TRADES_PATH.write_text(
        json.dumps(trades, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(
        f"Registered {manager_name}: {out_player} -> {in_player}; "
        f"counted={counted_trade}; banked={credited_out_goals}; "
        f"incoming at acquisition={current_in_goals}"
    )


if __name__ == "__main__":
    main()
