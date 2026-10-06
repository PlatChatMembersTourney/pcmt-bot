"""Map-aware team Elo for each event, printed as a table.

Run: python elo.py            (every event in events.json)
     python elo.py s3-na      (only the events listed)

Each team has an overall rating R plus an offset M[map], and its rating on a map is R + M[map].
Every map played is one game. A result moves R by K_OVERALL and that map's offset by K_MAP, then the offsets are
re-centered so their games-weighted mean is 0:
  mean_offset = sum(games[m] * M[m]) / sum(games[m])   over maps played
  R += mean_offset
  M[m] -= mean_offset                                  for every map played
"""
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

EVENTS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pcmt2", "src", "data"))
ELO_START = 1500
K_OVERALL = 32
K_MAP = 8
K_FLOOR = 0.5
K_DECAY_MAPS = 20
DOMINANCE_FULL = 8


def new_team():
    return {"rating": ELO_START, "maps": {}, "played": 0}


def k_scale(elo):
    """
    Shrinks K from full toward K_FLOOR as a team plays more maps - halfway there after K_DECAY_MAPS maps.
    """
    return K_FLOOR + (1 - K_FLOOR) * K_DECAY_MAPS / (K_DECAY_MAPS + elo["played"])


def dominance(m):
    """
    Scales a map's result by its round margin - a DOMINANCE_FULL-round win (13-5) counts fully, closer maps less, bigger wins more.
    """
    return math.log(abs(m["score1"] - m["score2"]) + 1) / math.log(DOMINANCE_FULL + 1)


def map_rating(elo, map_name):
    """
    The team's rating on a map. R alone if they haven't played it.
    """
    return elo["rating"] + elo["maps"].get(map_name, {}).get("offset", 0)


def win_chance(a, b, map_name):
    """
    Chance team a beats team b on a map.
    """
    return 1 / (1 + 10 ** ((map_rating(b, map_name) - map_rating(a, map_name)) / 400))


def recenter(elo):
    played = elo["maps"].values()
    games = sum(m["games"] for m in played)
    if games == 0:
        return
    mean_offset = sum(m["games"] * m["offset"] for m in played) / games
    elo["rating"] += mean_offset
    for m in played:
        m["offset"] -= mean_offset


def parse_date(match):
    date = datetime.fromisoformat(match["date"].replace("Z", "+00:00"))
    return date if date.tzinfo else date.replace(tzinfo=timezone.utc)


def snapshot(elo):
    return {"rating": elo["rating"], "maps": {name: map_rating(elo, name) for name in elo["maps"]}}


def compute_elo(matches, history=None):
    """
    Ratings team after every completed map, played in date order
    If history is a dict, each team's matches are added to history[abbr] with their ratings before and after
    """
    elos = {}
    # Reverse so newest first
    for match in sorted(reversed(matches), key=parse_date):
        if not match.get("completed"):
            continue
        team1 = elos.setdefault(match["team1"], new_team())
        team2 = elos.setdefault(match["team2"], new_team())
        before = {match["team1"]: snapshot(team1), match["team2"]: snapshot(team2)}
        chances = [(m, win_chance(team1, team2, m["name"])) for m in match.get("maps", []) if m["score1"] != m["score2"]]
        for elo, sign in ((team1, 1), (team2, -1)):
            scale = k_scale(elo)
            surprises = [(m["name"], sign * ((1 if m["score1"] > m["score2"] else 0) - chance) * dominance(m)) for m, chance in chances]
            total = sum(s for _, s in surprises)
            elo["rating"] += K_OVERALL * scale * total
            for name, surprise in surprises:
                elo["played"] += 1
                if re.fullmatch(r"Map \d+", name):
                    continue
                entry = elo["maps"].setdefault(name, {"offset": 0, "games": 0})
                entry["offset"] += K_MAP * scale * surprise - K_OVERALL * scale * (total - surprise)
                entry["games"] += 1
            recenter(elo)
        if history is not None:
            for abbr, opp, elo, first in ((match["team1"], match["team2"], team1, True), (match["team2"], match["team1"], team2, False)):
                history.setdefault(abbr, []).append({
                    "match": match,
                    "first": first,
                    "opponent": opp,
                    "opponent_rating": before[opp]["rating"],
                    "maps": [(m, chance if first else 1 - chance) for m, chance in chances],
                    "before": before[abbr],
                    "after": snapshot(elo),
                })
    return elos


def print_team(event, abbr, entries):
    print(f"\n{event['name']} - {abbr}")
    for e in entries:
        match, before, after, first = e["match"], e["before"], e["after"], e["first"]
        won, lost = (match["score1"], match["score2"]) if first else (match["score2"], match["score1"])
        result = "W" if won > lost else "L"
        print(f"\n{match['date'][:10]}  {match['stage']}  vs {e['opponent']} ({round(e['opponent_rating'])})  {result} {won}-{lost}")
        for m, chance in e["maps"]:
            us, them = (m["score1"], m["score2"]) if first else (m["score2"], m["score1"])
            print(f"  {'W' if us > them else 'L'} {m['name']:<9} {us:>2}-{them:<2}  win chance {chance:.0%}")
        change = after["rating"] - before["rating"]
        print(f"  Elo   {round(before['rating'])} -> {round(after['rating'])} ({change:+.0f})")
        maps = []
        for name in sorted(after["maps"], key=lambda n: -after["maps"][n]):
            old = before["maps"].get(name, before["rating"])
            maps.append(f"{name} {round(after['maps'][name])} ({after['maps'][name] - old:+.0f})")
        if maps:
            print(f"  Maps  {'  '.join(maps)}")


def print_event(event, group_elos, elos):
    ranked = sorted(elos.items(), key=lambda kv: kv[1]["rating"], reverse=True)
    width = max(len("Team"), *(len(abbr) for abbr, _ in ranked))

    print(f"\n{event['name']}")
    print(f"{'Team'.ljust(width)}  Groups  Overall")
    for abbr, elo in ranked:
        groups = str(round(group_elos[abbr]["rating"])) if abbr in group_elos else "-"
        print(f"{abbr.ljust(width)}  {groups.rjust(6)}  {str(round(elo['rating'])).rjust(7)}")


def print_maps(elos, title, top=10):
    games = {}
    for elo in elos.values():
        for name, entry in elo["maps"].items():
            games[name] = games.get(name, 0) + entry["games"]
    maps = sorted(games, key=lambda name: -games[name])
    if not maps:
        return

    columns = []
    for name in maps:
        teams = sorted((abbr for abbr, elo in elos.items() if name in elo["maps"]), key=lambda abbr: -map_rating(elos[abbr], name))
        columns.append([name] + [f"{abbr} {round(map_rating(elos[abbr], name))}" for abbr in teams[:top]])
    rows = max(len(c) for c in columns)
    widths = [max(len(cell) for cell in c) for c in columns]

    print(f"\nTop {top} per map - {title}")
    for i in range(rows):
        prefix = "    " if i == 0 else f"{i:>2}. "
        print(prefix + "  ".join((c[i] if i < len(c) else "").ljust(w) for c, w in zip(columns, widths)).rstrip())


def main():
    with open(os.path.join(EVENTS_DIR, "events.json"), encoding="utf-8") as fp:
        events = json.load(fp)

    args = sys.argv[1:]
    team = None
    if "--team" in args:
        i = args.index("--team")
        if i + 1 >= len(args):
            sys.exit("Usage: python elo.py [event ids...] --team ABBR")
        team = args[i + 1].lower()
        args = args[:i] + args[i + 2:]

    wanted = args
    for event_id in wanted:
        if not any(e["id"] == event_id for e in events):
            print(f"No event \"{event_id}\" - ids: {', '.join(e['id'] for e in events)}", file=sys.stderr)

    for event in events:
        if wanted and event["id"] not in wanted:
            continue
        path = os.path.join(EVENTS_DIR, event["path"], "matches", "matches.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fp:
            matches = json.load(fp)
        if team:
            history = {}
            compute_elo(matches, history)
            for abbr, entries in history.items():
                if abbr.lower() == team:
                    print_team(event, abbr, entries)
            continue
        elos = compute_elo(matches)
        group_elos = compute_elo([m for m in matches if m.get("stage", "").startswith("Group")])
        if elos:
            print_event(event, group_elos, elos)
            print_maps(group_elos, "Groups")
            print_maps(elos, "Overall")


if __name__ == "__main__":
    main()
