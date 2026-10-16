#!/usr/bin/env python3
import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LEAGUE_PATH = ROOT / "data" / "league.json"
TRADES_PATH = ROOT / "data" / "trades.json"
STATS_PATH = ROOT / "data" / "stats.json"
HISTORY_PATH = ROOT / "data" / "history.json"
LEDGER_PATH = ROOT / "data" / "game_ledger.json"

LOCAL_TZ = ZoneInfo("Europe/Bratislava")
RECHECK_DAYS = 14
USER_AGENT = "NHL-Strelci-26-27/4.0"


def load(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_json(url, attempts=4):
    last_error = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=35) as response:
                return json.load(response)
        except Exception as exc:
            last_error = exc
            if i == attempts - 1:
                break
            time.sleep(1.5 * (2 ** i))
    raise RuntimeError(f"NHL API failed after {attempts} attempts: {url}: {last_error}")


def fetch_summary(season, game_type):
    base = "https://api.nhle.com/stats/rest/en/skater/summary"
    params = {
        "isAggregate": "false",
        "isGame": "false",
        "start": "0",
        "limit": "-1",
        "sort": json.dumps(
            [{"property": "goals", "direction": "DESC"}],
            separators=(",", ":"),
        ),
        "cayenneExp": f"seasonId={season} and gameTypeId={game_type}",
    }
    return get_json(base + "?" + urllib.parse.urlencode(params)).get("data", [])


def fetch_score(game_date):
    return get_json(f"https://api-web.nhle.com/v1/score/{game_date}")


def extract_boxscore_goals(game_id):
    box = get_json(f"https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore")
    result = defaultdict(int)
    stats = box.get("playerByGameStats", {})

    for side in ("homeTeam", "awayTeam"):
        side_stats = stats.get(side, {}) or {}
        for group in ("forwards", "defense", "goalies"):
            for player in side_stats.get(group, []) or []:
                player_id = player.get("playerId")
                goals = int(player.get("goals", 0) or 0)
                if player_id and goals:
                    result[int(player_id)] += goals

    return dict(result), box


def daterange(start_date, end_date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def season_scan_start(season):
    start_year = int(str(season)[:4])
    # September is intentionally broad: score endpoint is cheap and gameType=2
    # filters out preseason. This avoids hard-coding opening-night dates.
    return date(start_year, 9, 1)


def is_started_state(state):
    return (state or "").upper() not in {"FUT", "PRE"}


def refresh_ledger(league, full=False):
    season = str(league["season"])
    game_type = int(league.get("gameTypeId", 2))
    local_today = datetime.now(LOCAL_TZ).date()

    ledger = load(
        LEDGER_PATH,
        {
            "season": season,
            "gameTypeId": game_type,
            "updatedAt": None,
            "games": {},
        },
    )

    if (
        str(ledger.get("season")) != season
        or int(ledger.get("gameTypeId", game_type)) != game_type
    ):
        ledger = {
            "season": season,
            "gameTypeId": game_type,
            "updatedAt": None,
            "games": {},
        }
        full = True

    if full or not ledger.get("games"):
        ledger["games"] = {}
        start_date = season_scan_start(season)
    else:
        start_date = max(
            season_scan_start(season),
            local_today - timedelta(days=RECHECK_DAYS),
        )

    end_date = local_today
    refreshed_games = 0

    for day in daterange(start_date, end_date):
        day_s = day.isoformat()
        score = fetch_score(day_s)

        for game in score.get("games", []) or []:
            if int(game.get("gameType") or 0) != game_type:
                continue

            game_id = game.get("id")
            state = game.get("gameState") or ""
            if not game_id or not is_started_state(state):
                continue

            goals, box = extract_boxscore_goals(game_id)
            game_date = (
                box.get("gameDate")
                or game.get("gameDate")
                or day_s
            )
            start_utc = (
                box.get("startTimeUTC")
                or game.get("startTimeUTC")
            )

            ledger["games"][str(game_id)] = {
                "gameId": int(game_id),
                "gameDate": game_date,
                "startTimeUTC": start_utc,
                "gameState": box.get("gameState") or state,
                "goals": {str(pid): int(g) for pid, g in goals.items()},
                "checkedAt": datetime.now(timezone.utc).isoformat(),
            }
            refreshed_games += 1

    ledger["updatedAt"] = datetime.now(timezone.utc).isoformat()
    dump(LEDGER_PATH, ledger)
    return ledger, refreshed_games


def ledger_totals(ledger):
    totals = defaultdict(int)
    by_date = defaultdict(lambda: defaultdict(int))

    for game in (ledger.get("games") or {}).values():
        game_date = game.get("gameDate")
        for pid_s, goals in (game.get("goals") or {}).items():
            pid = int(pid_s)
            g = int(goals or 0)
            totals[pid] += g
            if game_date:
                by_date[game_date][pid] += g

    return dict(totals), {
        d: dict(v) for d, v in by_date.items()
    }


def validate_league(league):
    managers = league.get("managers", [])
    slots = [s for m in managers for s in m.get("roster", [])]
    expected = len(managers) * 8

    if len(slots) != expected:
        raise ValueError(
            f"Roster integrity error: expected {expected} slots, got {len(slots)}."
        )

    missing = [s.get("player") for s in slots if not s.get("playerId")]
    if missing:
        raise ValueError("Missing playerId for: " + ", ".join(missing))

    ids = [int(s["playerId"]) for s in slots]
    if len(ids) != len(set(ids)):
        duplicates = sorted({x for x in ids if ids.count(x) > 1})
        raise ValueError(f"Duplicate NHL playerId in league: {duplicates}")


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def resolve_player_id_by_name(name, cache):
    if name in cache:
        return cache[name]

    url = (
        "https://search.d3.nhle.com/api/v1/search/player?"
        + urllib.parse.urlencode(
            {
                "culture": "en-us",
                "limit": 20,
                "q": name,
                "active": "true",
            }
        )
    )
    candidates = get_json(url)
    exact = [
        c for c in candidates
        if norm(c.get("name")) == norm(name)
    ]
    if len(exact) != 1:
        raise ValueError(f"Could not uniquely resolve NHL player: {name}")

    cache[name] = int(exact[0]["playerId"])
    return cache[name]


def normalized_trade_records(trades, league):
    name_id_cache = {}
    for manager in league.get("managers", []):
        for slot in manager.get("roster", []):
            if slot.get("player") and slot.get("playerId"):
                name_id_cache[slot["player"]] = int(slot["playerId"])

    records = []
    for raw in trades.get("records", []) or []:
        if raw.get("type") != "regular_trade":
            continue

        rec = dict(raw)
        if not rec.get("outPlayerId"):
            rec["outPlayerId"] = resolve_player_id_by_name(
                rec["outPlayer"], name_id_cache
            )
        if not rec.get("inPlayerId"):
            rec["inPlayerId"] = resolve_player_id_by_name(
                rec["inPlayer"], name_id_cache
            )
        records.append(rec)

    records.sort(
        key=lambda r: (
            r.get("date") or "",
            r.get("timestamp") or "",
            int(r.get("githubIssue") or 0),
        )
    )
    return records


def build_initial_slots(league, regular_trades):
    slots = {}

    for manager in league.get("managers", []):
        slots[manager["name"]] = [
            {
                "slotIndex": idx,
                "player": slot["player"],
                "playerId": int(slot["playerId"]),
            }
            for idx, slot in enumerate(manager.get("roster", []))
        ]

    for rec in reversed(regular_trades):
        manager = rec["manager"]
        in_id = int(rec["inPlayerId"])
        out_id = int(rec["outPlayerId"])
        found = False

        for slot in slots[manager]:
            if int(slot["playerId"]) == in_id:
                slot["player"] = rec["outPlayer"]
                slot["playerId"] = out_id
                found = True
                break

        if not found:
            raise ValueError(
                f"Trade replay error: cannot reverse {manager} "
                f"{rec['outPlayer']} -> {rec['inPlayer']}"
            )

    return slots


def parse_trade_time(rec):
    stamp = rec.get("timestamp")
    if stamp:
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            pass

    # Legacy fallback: treat the trade as effective at the start of its recorded UTC date.
    return datetime.fromisoformat((rec.get("date") or "1970-01-01") + "T00:00:00+00:00")


def parse_game_start(game):
    stamp = game.get("startTimeUTC")
    if stamp:
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            pass

    # NHL boxscores should always expose startTimeUTC. This fallback keeps
    # legacy ledger rows deterministic.
    return datetime.fromisoformat((game.get("gameDate") or "1970-01-01") + "T12:00:00+00:00")


def replay_league(league, trades, ledger):
    regular_trades = normalized_trade_records(trades, league)
    for rec in regular_trades:
        rec["_effectiveAt"] = parse_trade_time(rec)

    slots = build_initial_slots(league, regular_trades)

    manager_totals = {
        m["name"]: 0 for m in league.get("managers", [])
    }
    slot_totals = {
        m["name"]: [0] * len(m.get("roster", []))
        for m in league.get("managers", [])
    }
    slot_banked = {
        m["name"]: [0] * len(m.get("roster", []))
        for m in league.get("managers", [])
    }
    slot_acquisition_floor = {
        m["name"]: [0] * len(m.get("roster", []))
        for m in league.get("managers", [])
    }
    player_cumulative = defaultdict(int)
    credited_by_manager_player = {
        m["name"]: defaultdict(int)
        for m in league.get("managers", [])
    }

    # Daily credited goals are tracked by the player who owned the slot when
    # the game began. This prevents a same-day trade from moving earlier goals.
    daily_by_date = defaultdict(
        lambda: {m["name"]: 0 for m in league.get("managers", [])}
    )
    daily_player_by_manager = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )

    history_snapshots = []

    def apply_trade(rec):
        manager = rec["manager"]
        out_id = int(rec["outPlayerId"])
        in_id = int(rec["inPlayerId"])

        for slot in slots[manager]:
            if int(slot["playerId"]) == out_id:
                idx = int(slot["slotIndex"])

                # Everything already credited to this slot stays banked.
                slot_banked[manager][idx] = int(slot_totals[manager][idx])

                # Incoming player's already-scored season goals never count
                # for the new manager.
                slot_acquisition_floor[manager][idx] = int(
                    player_cumulative.get(in_id, 0)
                )

                slot["player"] = rec["inPlayer"]
                slot["playerId"] = in_id
                return

        raise ValueError(
            f"Trade replay error: cannot apply {manager} "
            f"{rec['outPlayer']} -> {rec['inPlayer']}"
        )

    games = sorted(
        (ledger.get("games") or {}).values(),
        key=lambda g: (parse_game_start(g), int(g.get("gameId") or 0)),
    )

    trade_idx = 0
    current_snapshot_date = None

    def snapshot_for_date(game_date):
        league_total = sum(manager_totals.values())
        n = len(manager_totals)
        value_per_goal = league.get("valuePerGoal", 1)
        return {
            "date": game_date,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "managers": {
                manager: {
                    "goals": goals,
                    "net": (n * goals - league_total) * value_per_goal,
                }
                for manager, goals in manager_totals.items()
            },
        }

    for game in games:
        game_start = parse_game_start(game)

        # A trade is effective for games that START after the registration
        # timestamp. This makes the rule deterministic and avoids ownership
        # changing mid-game.
        while (
            trade_idx < len(regular_trades)
            and regular_trades[trade_idx]["_effectiveAt"] <= game_start
        ):
            apply_trade(regular_trades[trade_idx])
            trade_idx += 1

        owner_by_player = {}
        for manager, manager_slots in slots.items():
            for slot in manager_slots:
                owner_by_player[int(slot["playerId"])] = (
                    manager,
                    int(slot["slotIndex"]),
                )

        game_date = game.get("gameDate") or game_start.date().isoformat()

        # Close previous date after all its games have been processed.
        if current_snapshot_date is not None and game_date != current_snapshot_date:
            history_snapshots.append(snapshot_for_date(current_snapshot_date))
        current_snapshot_date = game_date

        for pid_s, goals in (game.get("goals") or {}).items():
            pid = int(pid_s)
            g = int(goals or 0)
            if not g:
                continue

            owner = owner_by_player.get(pid)
            if owner:
                manager, slot_index = owner
                manager_totals[manager] += g
                slot_totals[manager][slot_index] += g
                credited_by_manager_player[manager][pid] += g
                daily_by_date[game_date][manager] += g
                daily_player_by_manager[game_date][manager][pid] += g

            # Season totals advance for every NHL scorer, including free
            # agents. This is required for a correct later acquisition floor.
            player_cumulative[pid] += g

    if current_snapshot_date is not None:
        history_snapshots.append(snapshot_for_date(current_snapshot_date))

    # Apply trades after the latest game too, so current-slot accounting
    # matches the current roster even if no game has occurred since.
    while trade_idx < len(regular_trades):
        apply_trade(regular_trades[trade_idx])
        trade_idx += 1

    current_slot_totals = {}
    current_accounting = {}
    for manager in league.get("managers", []):
        name = manager["name"]
        current_slot_totals[name] = {}
        current_accounting[name] = {}

        for idx, slot in enumerate(manager.get("roster", [])):
            pid_s = str(int(slot["playerId"]))
            current_slot_totals[name][pid_s] = int(slot_totals[name][idx])
            current_accounting[name][pid_s] = {
                "bankedGoals": int(slot_banked[name][idx]),
                "goalsAtAcquisition": int(slot_acquisition_floor[name][idx]),
                "creditedSlotGoals": int(slot_totals[name][idx]),
            }

    clean_daily = {
        game_date: {manager: int(goals) for manager, goals in values.items()}
        for game_date, values in daily_by_date.items()
    }
    clean_daily_players = {
        game_date: {
            manager: {str(pid): int(goals) for pid, goals in players.items()}
            for manager, players in managers.items()
        }
        for game_date, managers in daily_player_by_manager.items()
    }

    return {
        "managerTotals": {k: int(v) for k, v in manager_totals.items()},
        "currentSlotTotals": current_slot_totals,
        "currentAccounting": current_accounting,
        "dailyByDate": clean_daily,
        "dailyPlayerByManager": clean_daily_players,
        "history": {"snapshots": history_snapshots},
        "creditedByManagerPlayer": {
            manager: {
                str(pid): int(goals)
                for pid, goals in values.items()
            }
            for manager, values in credited_by_manager_player.items()
        },
    }


def repair_league_accounting(league, accounting):
    changes = []

    for manager in league.get("managers", []):
        manager_name = manager["name"]
        manager_accounting = accounting.get(manager_name, {})

        for slot in manager.get("roster", []):
            pid_s = str(int(slot["playerId"]))
            expected = manager_accounting.get(pid_s)
            if not expected:
                continue

            expected_banked = int(expected["bankedGoals"])
            expected_floor = int(expected["goalsAtAcquisition"])
            old_banked = int(slot.get("bankedGoals", 0) or 0)
            old_floor = int(slot.get("goalsAtAcquisition", 0) or 0)

            if old_banked != expected_banked or old_floor != expected_floor:
                changes.append(
                    {
                        "manager": manager_name,
                        "player": slot["player"],
                        "playerId": int(slot["playerId"]),
                        "bankedGoals": [old_banked, expected_banked],
                        "goalsAtAcquisition": [old_floor, expected_floor],
                    }
                )
                slot["bankedGoals"] = expected_banked
                slot["goalsAtAcquisition"] = expected_floor

    if changes:
        dump(LEAGUE_PATH, league)

    return changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full",
        action="store_true",
        help="Rebuild every regular-season game from September 1.",
    )
    args = parser.parse_args()

    league = load(LEAGUE_PATH, {})
    trades = load(TRADES_PATH, {"records": []})
    previous = load(STATS_PATH, {"players": {}})

    validate_league(league)

    ledger, refreshed_games = refresh_ledger(
        league,
        full=args.full,
    )
    season_totals, by_date = ledger_totals(ledger)
    replay = replay_league(league, trades, ledger)
    accounting_repairs = repair_league_accounting(
        league,
        replay["currentAccounting"],
    )

    season = str(league["season"])
    game_type = int(league.get("gameTypeId", 2))
    summary_rows = fetch_summary(season, game_type)
    summary_by_id = {
        int(row["playerId"]): row
        for row in summary_rows
        if row.get("playerId") is not None
    }

    night_game_date = (
        datetime.now(LOCAL_TZ).date() - timedelta(days=1)
    ).isoformat()
    night_player_goals = by_date.get(night_game_date, {})
    manager_daily = replay["dailyByDate"].get(
        night_game_date,
        {m["name"]: 0 for m in league.get("managers", [])},
    )

    players = {}
    discrepancies = []

    for manager in league.get("managers", []):
        for slot in manager.get("roster", []):
            name = slot["player"]
            player_id = int(slot["playerId"])
            row = summary_by_id.get(player_id)
            old = previous.get("players", {}).get(name, {})
            official_goals = int(season_totals.get(player_id, 0))
            summary_goals = (
                int(row.get("goals", 0) or 0)
                if row
                else None
            )

            if summary_goals is not None and summary_goals != official_goals:
                discrepancies.append(
                    {
                        "player": name,
                        "playerId": player_id,
                        "ledgerGoals": official_goals,
                        "summaryGoals": summary_goals,
                        "difference": official_goals - summary_goals,
                    }
                )

            players[name] = {
                "playerId": player_id,
                "goals": official_goals,
                "gamesPlayed": int(
                    (row or {}).get(
                        "gamesPlayed",
                        old.get("gamesPlayed", 0),
                    )
                    or 0
                ),
                "team": (
                    (row or {}).get("teamAbbrevs")
                    or old.get("team", "")
                ),
                "shots": int(
                    (row or {}).get(
                        "shots",
                        old.get("shots", 0),
                    )
                    or 0
                ),
                "shootingPct": (
                    (row or {}).get("shootingPct")
                    if row
                    else old.get("shootingPct")
                ),
                "deltaGoals": int(
                    replay["dailyPlayerByManager"]
                    .get(night_game_date, {})
                    .get(manager["name"], {})
                    .get(str(player_id), 0)
                ),
                "summaryGoals": summary_goals,
                "goalSource": "NHL gamecenter boxscores",
                "found": True,
            }

    now = datetime.now(timezone.utc).isoformat()
    stats_out = {
        "season": season,
        "gameTypeId": game_type,
        "updatedAt": now,
        "source": "NHL Gamecenter boxscores · per-game ledger · playerId locked",
        "nightGameDate": night_game_date,
        "players": players,
        "managerTotals": replay["managerTotals"],
        "managerDaily": {
            k: int(v) for k, v in manager_daily.items()
        },
        "currentSlotTotals": replay["currentSlotTotals"],
        "currentSlotDaily": replay["dailyPlayerByManager"].get(
            night_game_date,
            {},
        ),
        "audit": {
            "ledgerGames": len(ledger.get("games") or {}),
            "refreshedGamesThisRun": refreshed_games,
            "summaryDiscrepancies": discrepancies,
            "summaryDiscrepancyCount": len(discrepancies),
            "mode": "full" if args.full else "incremental",
            "recheckDays": RECHECK_DAYS,
            "accountingRepairs": accounting_repairs,
            "accountingRepairCount": len(accounting_repairs),
        },
        "message": "OK",
    }

    dump(STATS_PATH, stats_out)
    dump(HISTORY_PATH, replay["history"])

    print(
        f"BOX_LEDGER update: players={len(players)}; "
        f"ledgerGames={stats_out['audit']['ledgerGames']}; "
        f"refreshed={refreshed_games}; "
        f"summaryDiffs={len(discrepancies)}; "
        f"accountingRepairs={len(accounting_repairs)}; "
        f"nightDate={night_game_date}; "
        f"leagueGoals={sum(replay['managerTotals'].values())}"
    )


if __name__ == "__main__":
    main()
