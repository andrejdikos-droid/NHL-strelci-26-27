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
        if forced_id is not None:
            old_player_id = int(old.get("playerId") or 0)
            # If previous data belonged to a different namesake, discard it.
            if old_player_id not in (0, forced_id):
                old_goals = 0
                old = {}

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

    fix_known_history_errors(history, n, value_per_goal)

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
