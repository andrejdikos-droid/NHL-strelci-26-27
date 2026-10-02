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


def get_json(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent":"NHL-Strelci-26-27/2.0","Accept":"application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def fetch_stats(season, game_type):
    base = "https://api.nhle.com/stats/rest/en/skater/summary"
    params = {
        "isAggregate":"false",
        "isGame":"false",
        "start":"0",
        "limit":"-1",
        "sort":json.dumps([{"property":"goals","direction":"DESC"}], separators=(",",":")),
        "cayenneExp":f"seasonId={season} and gameTypeId={game_type}"
    }
    return get_json(base + "?" + urllib.parse.urlencode(params)).get("data", [])


def canonical_name_from_landing(data):
    first = data.get("firstName", {})
    last = data.get("lastName", {})
    if isinstance(first, dict):
        first = first.get("default", "")
    if isinstance(last, dict):
        last = last.get("default", "")
    return f"{first} {last}".strip()


def resolve_incoming_player(raw_name):
    # Optional explicit syntax for ambiguous names: "Jason Robertson #8480027"
    m = re.match(r"^(.*?)\s*#(\d{7})\s*$", raw_name.strip())
    if m:
        typed_name = m.group(1).strip()
        player_id = int(m.group(2))
        landing = get_json(f"https://api-web.nhle.com/v1/player/{player_id}/landing")
        canonical = canonical_name_from_landing(landing) or typed_name
        if typed_name and norm(typed_name) != norm(canonical):
            raise ValueError(
                f"Player ID {player_id} belongs to {canonical}, not {typed_name}."
            )
        return canonical, player_id

    url = (
        "https://search.d3.nhle.com/api/v1/search/player?"
        + urllib.parse.urlencode({
            "culture":"en-us",
            "limit":20,
            "q":raw_name.strip(),
            "active":"true"
        })
    )
    candidates = get_json(url)
    exact = [c for c in candidates if norm(c.get("name")) == norm(raw_name)]

    if len(exact) == 1:
        return exact[0]["name"], int(exact[0]["playerId"])

    if len(exact) > 1:
        choices = ", ".join(
            f"{c.get('name')} #{c.get('playerId')} ({c.get('teamAbbrev') or '—'})"
            for c in exact
        )
        raise ValueError(
            "Ambiguous NHL name. Enter NAME #PLAYERID. Candidates: " + choices
        )

    raise ValueError(f"NHL player not found exactly: {raw_name}")


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
    raw_in_player = fields["IN"]
    counted_trade = fields["COUNT_TRADE"].lower() == "true"
    note = fields.get("NOTE", "")
    if note == "-":
        note = ""

    league = load(LEAGUE_PATH, {})
    trades = load(TRADES_PATH, {"records":[]})

    manager = next(
        (m for m in league.get("managers", []) if m.get("name") == manager_name),
        None
    )
    if not manager:
        raise ValueError(f"Unknown manager: {manager_name}")

    slot = next(
        (s for s in manager.get("roster", []) if s.get("player") == out_player),
        None
    )
    if not slot:
        raise ValueError(f"{out_player} is not on {manager_name}'s current roster.")
    if not slot.get("playerId"):
        raise ValueError(f"OUT player has no locked playerId: {out_player}")

    in_player, in_player_id = resolve_incoming_player(raw_in_player)
    out_player_id = int(slot["playerId"])

    if out_player_id == in_player_id:
        raise ValueError("OUT and IN player are the same NHL player.")

    for other_manager in league.get("managers", []):
        for other_slot in other_manager.get("roster", []):
            if int(other_slot.get("playerId") or 0) == in_player_id:
                raise ValueError(
                    f"{in_player} is already rostered by {other_manager.get('name')}."
                )

    max_trades = int(league.get("rules", {}).get("maxTradesPerManager", 4))
    if counted_trade and int(manager.get("tradesUsed", 0)) >= max_trades:
        raise ValueError(f"{manager_name} has already used all {max_trades} trades.")

    rows = fetch_stats(league["season"], league.get("gameTypeId", 2))
    by_id = {
        int(row["playerId"]): row
        for row in rows
        if row.get("playerId") is not None
    }

    out_row = by_id.get(out_player_id)
    in_row = by_id.get(in_player_id)

    current_out_goals = int(out_row.get("goals", 0) or 0) if out_row else 0
    current_in_goals = int(in_row.get("goals", 0) or 0) if in_row else 0

    previous_banked = int(slot.get("bankedGoals", 0) or 0)
    acquired_at = int(slot.get("goalsAtAcquisition", 0) or 0)
    credited_out_goals = previous_banked + max(0, current_out_goals - acquired_at)

    if dry_run:
        summary = (
            f"DRY RUN OK\n"
            f"Manager: {manager_name}\n"
            f"OUT: {out_player} #{out_player_id}\n"
            f"IN: {in_player} #{in_player_id}\n"
            f"Banked goals after OUT: {credited_out_goals}\n"
            f"Incoming goals at acquisition: {current_in_goals}\n"
            f"Trade counter would become: "
            f"{int(manager.get('tradesUsed',0)) + (1 if counted_trade else 0)}/{max_trades}\n"
            f"No files were changed."
        )
        print(summary)
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            with open(step_summary, "a", encoding="utf-8") as fh:
                fh.write("## ✅ Trade dry run passed\n\n")
                fh.write(f"- **Manager:** {manager_name}\n")
                fh.write(f"- **OUT:** {out_player} `#{out_player_id}`\n")
                fh.write(f"- **IN:** {in_player} `#{in_player_id}`\n")
                fh.write(f"- **Banked goals after OUT:** {credited_out_goals}\n")
                fh.write(f"- **Incoming goals at acquisition:** {current_in_goals}\n")
                fh.write(
                    f"- **Trade counter would become:** "
                    f"{int(manager.get('tradesUsed',0)) + (1 if counted_trade else 0)}/{max_trades}\n"
                )
                fh.write("- **Data changed:** NO\n")
        return

    slot["player"] = in_player
    slot["playerId"] = in_player_id
    slot["bankedGoals"] = credited_out_goals
    slot["goalsAtAcquisition"] = current_in_goals

    trade_number = None
    if counted_trade:
        manager["tradesUsed"] = int(manager.get("tradesUsed", 0)) + 1
        trade_number = manager["tradesUsed"]

    now = datetime.now(timezone.utc)
    trades.setdefault("records", []).append({
        "date":now.date().isoformat(),
        "timestamp":now.isoformat(),
        "manager":manager_name,
        "outPlayer":out_player,
        "outPlayerId":out_player_id,
        "inPlayer":in_player,
        "inPlayerId":in_player_id,
        "countedTrade":counted_trade,
        "tradeNumber":trade_number,
        "bankedGoals":credited_out_goals,
        "incomingGoalsAtAcquisition":current_in_goals,
        "type":"regular_trade" if counted_trade else "special_replacement",
        "note":note,
        "githubIssue":issue.get("number")
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
        f"Registered {manager_name}: {out_player} #{out_player_id} -> "
        f"{in_player} #{in_player_id}; counted={counted_trade}; "
        f"banked={credited_out_goals}; incoming={current_in_goals}"
    )


if __name__ == "__main__":
    main()
