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

PLAYER_ID_OVERRIDES = {
    "Jason Robertson": 8480027,
    "Jack Hughes": 8481559,
    "Will Smith": 8484227,
    "Sebastian Aho": 8478427,
}

NAME_ALIASES = {
    "John-Jason Peterka": ["JJ Peterka", "J.J. Peterka"],
}


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
    forced_id = PLAYER_ID_OVERRIDES.get(name)
    if forced_id is not None:
        return next(
            (row for row in rows if int(row.get("playerId") or 0) == forced_id),
            None
        )

    target_norms = {norm(name)}
    target_norms.update(norm(x) for x in NAME_ALIASES.get(name, []))

    exact = [
        row for row in rows
        if norm(row.get("skaterFullName")) in target_norms
    ]

    if len(exact) == 1:
        return exact[0]

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
    dry_run = title.startswith("[TRADE-TEST]")
    if not (title.startswith("[TRADE]") or dry_run):
        raise ValueError("Issue is not a trade request.")

    fields = parse_body(issue.get("body", ""))

    manager_name = fields["MANAGER"]
    out_player = fields["OUT"]
    in_player = fields["IN"]
    counted_trade = fields["COUNT_TRADE"].lower() == "true"
