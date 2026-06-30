#!/usr/bin/env python3
import json
import os
import re
import sys
import glob
from collections import defaultdict

from data_helpers import EVENTS_DIR as BOT_EVENTS_DIR
from agents import generate_agent_stats

# Bot layout (current): events/<region>/<season>/, individual match files flat
# in matches/, combined matches.json in the season folder.
# To target the website later, set:
#   EVENTS_DIR = r"D:/PCMT/pcmt/data/events"
#   SEASON_FIRST = True                  (events/<season>/<region>/)
#   INDIVIDUAL_SUBDIR = "individual"     (matches/individual/<id>.json)
#   MATCHES_JSON_IN_MATCHES_DIR = True   (matches/matches.json)
EVENTS_DIR = BOT_EVENTS_DIR
SEASON_FIRST = True
INDIVIDUAL_SUBDIR = None
MATCHES_JSON_IN_MATCHES_DIR = False

ALL_MAPS = [
    "Abyss", "Ascent", "Bind", "Breeze", "Corrode", "Fracture", "Haven",
    "Icebox", "Lotus", "Pearl", "Split", "Summit", "Sunset",
]


def season_dir(region, season):
    if SEASON_FIRST:
        return os.path.join(EVENTS_DIR, season, region)
    return os.path.join(EVENTS_DIR, region, season)


def individual_dir(region, season):
    base = os.path.join(season_dir(region, season), "matches")
    return os.path.join(base, INDIVIDUAL_SUBDIR) if INDIVIDUAL_SUBDIR else base


def matches_json_path(region, season):
    sd = season_dir(region, season)
    if MATCHES_JSON_IN_MATCHES_DIR:
        return os.path.join(sd, "matches", "matches.json")
    return os.path.join(sd, "matches.json")


def discover_events():
    """Walk two levels under EVENTS_DIR, returning (region, season) pairs."""
    out = []
    if not os.path.isdir(EVENTS_DIR):
        return out
    for outer in sorted(os.listdir(EVENTS_DIR)):
        outer_path = os.path.join(EVENTS_DIR, outer)
        if not os.path.isdir(outer_path):
            continue
        for inner in sorted(os.listdir(outer_path)):
            if not os.path.isdir(os.path.join(outer_path, inner)):
                continue
            region, season = (inner, outer) if SEASON_FIRST else (outer, inner)
            out.append((region, season))
    return out


def compute_rating(k, d, a, kast, adr, fk, fd, total_rounds):
    if total_rounds == 0:
        return 0.0
    kpr = k / total_rounds
    dpr = d / total_rounds
    apr = a / total_rounds
    return round(
        0.898 * kpr
        + 0.228 * apr
        + (-0.434) * dpr
        + 0.0025 * (adr - 140 * kpr)
        + 0.434 * (1 - dpr)
        + 0.313 * kast
        + 0.175, 3)


def combine_matches(region, season):
    ind_dir = individual_dir(region, season)
    out_path = matches_json_path(region, season)

    if not os.path.isdir(ind_dir):
        return []

    files = sorted(glob.glob(os.path.join(ind_dir, "*.json")))
    matches = []
    for f in files:
        # Skip matches.json if it lives in the same dir as the individual files.
        if os.path.abspath(f) == os.path.abspath(out_path):
            continue
        with open(f) as fp:
            try:
                matches.append(json.load(fp))
            except json.JSONDecodeError:
                print(f"  WARN: skipping invalid JSON: {os.path.basename(f)}")

    matches.sort(key=lambda m: m.get("date", "0000-00-00"), reverse=True)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fp:
        json.dump(matches, fp, indent=2)

    print(f"  matches.json: {len(matches)} matches")
    return matches


def generate_player_stats(event_dir, matches):
    completed = [m for m in matches if m.get("completed")]
    if not completed:
        print("  player-stats.json: no completed matches, skipping")
        return

    teams_path = os.path.join(event_dir, "teams.json")
    home_team = {}
    team_groups = {}
    if os.path.exists(teams_path):
        with open(teams_path) as fp:
            teams = json.load(fp)
        for abbr, t in teams.items():
            for p in t.get("players", []):
                home_team[p.lower()] = (abbr, t.get("name", abbr))
            if t.get("group"):
                team_groups[abbr] = t["group"]

    stages = set()
    for m in completed:
        stages.add(m.get("stage", "Unknown"))

    all_stages = ["Overall"] + sorted(stages)
    result = {}

    for stage_filter in all_stages:
        if stage_filter == "Overall":
            stage_matches = completed
            filter_by_group = False
        elif team_groups and stage_filter in team_groups.values():
            stage_matches = completed
            filter_by_group = True
        else:
            stage_matches = [m for m in completed if m.get("stage") == stage_filter]
            filter_by_group = False

        if not stage_matches:
            continue

        player_data = {}

        for match in stage_matches:
            for detail in match.get("mapDetails", []):
                map_rounds = detail.get("score1", 0) + detail.get("score2", 0)
                if map_rounds == 0:
                    continue
                for team_block in detail.get("stats", []):
                    for p in team_block.get("players", []):
                        name = p.get("Player", "")
                        if not name:
                            continue

                        ht = home_team.get(name.lower())
                        display_team = ht[0] if ht else "(sub)"
                        display_team_name = ht[1] if ht else "(sub)"

                        if filter_by_group:
                            player_group = team_groups.get(display_team)
                            if player_group and player_group != stage_filter:
                                continue

                        if name not in player_data:
                            player_data[name] = {
                                "team": display_team,
                                "teamName": display_team_name,
                                "maps": 0,
                                "rounds": 0,
                                "K": 0, "D": 0, "A": 0,
                                "FK": 0, "FD": 0,
                                "KMAX": 0,
                                "ACS_w": 0, "KAST_w": 0,
                                "ADR_w": 0, "HS_w": 0,
                            }
                        pd = player_data[name]
                        pd["maps"] += 1
                        pd["rounds"] += map_rounds
                        pd["K"] += p.get("K", 0)
                        pd["D"] += p.get("D", 0)
                        pd["A"] += p.get("A", 0)
                        pd["FK"] += p.get("FK", 0)
                        pd["FD"] += p.get("FD", 0)
                        pd["ACS_w"] += p.get("ACS", 0) * map_rounds
                        pd["KAST_w"] += p.get("KAST", 0) * map_rounds
                        pd["ADR_w"] += p.get("ADR", 0) * map_rounds
                        pd["HS_w"] += p.get("HS%", 0) * map_rounds
                        pd["KMAX"] = max(pd["KMAX"], p.get("K", 0))

        rows = []
        for name, pd in player_data.items():
            n = pd["maps"]
            total_r = pd["rounds"]
            acs = round(pd["ACS_w"] / total_r, 2) if total_r else 0
            kast = round(pd["KAST_w"] / total_r, 3) if total_r else 0
            adr = round(pd["ADR_w"] / total_r, 1) if total_r else 0
            hs = round(pd["HS_w"] / total_r, 3) if total_r else 0
            rating = compute_rating(
                pd["K"], pd["D"], pd["A"],
                kast, adr,
                pd["FK"], pd["FD"], total_r,
            )
            rows.append({
                "Player": name,
                "Team": pd["team"],
                "MP": n,
                "Rounds": total_r,
                "R1.0": rating,
                "ACS": round(acs),
                "K": pd["K"],
                "D": pd["D"],
                "A": pd["A"],
                "K/D": round(pd["K"] / pd["D"], 2) if pd["D"] else pd["K"],
                "PlusMinus": pd["K"] - pd["D"],
                "KPR": round(pd["K"] / total_r, 2) if total_r else 0,
                "DPR": round(pd["D"] / total_r, 2) if total_r else 0,
                "KAST": kast,
                "ADR": adr,
                "HS%": hs,
                "FK": pd["FK"],
                "FD": pd["FD"],
                "FKPR": round(pd["FK"] / total_r, 2) if total_r else 0,
                "FDPR": round(pd["FD"] / total_r, 2) if total_r else 0,
                "PlusMinus2": pd["FK"] - pd["FD"],
                "KMAX": pd["KMAX"],
            })

        rows.sort(key=lambda r: r["R1.0"], reverse=True)
        result[stage_filter] = rows

    out_path = os.path.join(event_dir, "player-stats.json")
    with open(out_path, "w") as fp:
        json.dump(result, fp, indent=2)

    total_players = len(result.get("Overall", []))
    print(f"  player-stats.json: {total_players} players, {len(result)} stages")


def generate_team_map_stats(event_dir, matches):
    completed = [m for m in matches if m.get("completed")]
    if not completed:
        print("  team-map-stats.json: no completed matches, skipping")
        return

    teams_path = os.path.join(event_dir, "teams.json")
    teams = {}
    if os.path.exists(teams_path):
        with open(teams_path) as fp:
            teams = json.load(fp)

    team_map_data = defaultdict(lambda: defaultdict(lambda: {
        "played": 0, "won": 0,
        "rounds": 0, "rounds_won": 0,
        "atk_rounds": 0, "atk_won": 0,
        "def_rounds": 0, "def_won": 0,
    }))

    team_bans = defaultdict(lambda: defaultdict(int))
    team_picks = defaultdict(lambda: defaultdict(int))

    for match in completed:
        t1 = match.get("team1", "")
        t2 = match.get("team2", "")
        veto = match.get("veto", "")

        if veto:
            for part in veto.split(","):
                part = part.strip()
                m = re.match(r"(\w+)\s+(bans?|picks?)\s+(.+)", part, re.I)
                if m:
                    team_abbr = m.group(1)
                    action = m.group(2).lower()
                    map_name = m.group(3).strip()
                    if "ban" in action:
                        team_bans[team_abbr][map_name] += 1
                    elif "pick" in action:
                        team_picks[team_abbr][map_name] += 1

        for detail in match.get("mapDetails", []):
            map_name = detail.get("name", "")
            if not map_name:
                continue
            s1 = detail.get("score1", 0)
            s2 = detail.get("score2", 0)
            total_rounds = s1 + s2

            tmd1 = team_map_data[t1][map_name]
            tmd1["played"] += 1
            tmd1["rounds"] += total_rounds
            tmd1["rounds_won"] += s1
            if s1 > s2:
                tmd1["won"] += 1

            tmd2 = team_map_data[t2][map_name]
            tmd2["played"] += 1
            tmd2["rounds"] += total_rounds
            tmd2["rounds_won"] += s2
            if s2 > s1:
                tmd2["won"] += 1

            for rnd in detail.get("rounds", []):
                winner = rnd.get("winner")
                side = rnd.get("side", "")

                if winner == 1:
                    winning_team = t1
                    losing_team = t2
                elif winner == 2:
                    winning_team = t2
                    losing_team = t1
                else:
                    continue

                w_data = team_map_data[winning_team][map_name]
                l_data = team_map_data[losing_team][map_name]

                if side == "atk":
                    w_data["atk_rounds"] += 1
                    w_data["atk_won"] += 1
                    l_data["def_rounds"] += 1
                elif side == "def":
                    w_data["def_rounds"] += 1
                    w_data["def_won"] += 1
                    l_data["atk_rounds"] += 1

    result = {}
    all_teams = set(team_map_data.keys())
    if teams:
        all_teams |= set(teams.keys())

    for team_abbr in sorted(all_teams):
        team_name = teams[team_abbr]["name"] if team_abbr in teams else team_abbr
        maps_data = dict(team_map_data.get(team_abbr, {}))

        empty = {
            "played": 0, "won": 0,
            "rounds": 0, "rounds_won": 0,
            "atk_rounds": 0, "atk_won": 0,
            "def_rounds": 0, "def_won": 0,
        }
        for map_name in ALL_MAPS:
            if map_name not in maps_data:
                maps_data[map_name] = dict(empty)

        for map_name in team_bans.get(team_abbr, {}):
            if map_name not in maps_data:
                maps_data[map_name] = dict(empty)
        for map_name in team_picks.get(team_abbr, {}):
            if map_name not in maps_data:
                maps_data[map_name] = dict(empty)

        total_atk = sum(d["atk_rounds"] for d in maps_data.values())
        total_atk_won = sum(d["atk_won"] for d in maps_data.values())
        total_def = sum(d["def_rounds"] for d in maps_data.values())
        total_def_won = sum(d["def_won"] for d in maps_data.values())

        map_rows = []
        for map_name in sorted(maps_data.keys()):
            d = maps_data[map_name]
            map_rows.append({
                "map": map_name,
                "pick": team_picks.get(team_abbr, {}).get(map_name, 0),
                "ban": team_bans.get(team_abbr, {}).get(map_name, 0),
                "played": d["played"],
                "won": d["won"],
                "winPct": round(d["won"] / d["played"], 3) if d["played"] else 0,
                "rounds": d["rounds"],
                "roundsWon": d["rounds_won"],
                "roundPct": round(d["rounds_won"] / d["rounds"], 3) if d["rounds"] else 0,
                "atkPct": round(d["atk_won"] / d["atk_rounds"], 3) if d["atk_rounds"] else 0,
                "defPct": round(d["def_won"] / d["def_rounds"], 3) if d["def_rounds"] else 0,
            })

        result[team_abbr] = {
            "team": team_abbr,
            "teamName": team_name,
            "overallAtkPct": round(total_atk_won / total_atk, 3) if total_atk else 0,
            "overallDefPct": round(total_def_won / total_def, 3) if total_def else 0,
            "maps": map_rows,
        }

    out_path = os.path.join(event_dir, "team-map-stats.json")
    with open(out_path, "w") as fp:
        json.dump(result, fp, indent=2)

    print(f"  team-map-stats.json: {len(result)} teams")


def generate_standings(event_dir, matches):
    completed = [m for m in matches if m.get("completed")]
    if not completed:
        print("  standings.json: no completed matches, skipping")
        return

    teams_path = os.path.join(event_dir, "teams.json")
    teams = {}
    if os.path.exists(teams_path):
        with open(teams_path) as fp:
            teams = json.load(fp)

    stages = {}
    for m in completed:
        stage = m.get("stage", "Unknown")
        if stage not in stages:
            stages[stage] = []
        stages[stage].append(m)

    def compute_standings(match_list, stage_name=None):
        team_stats = {}
        for abbr in teams:
            t = teams[abbr]
            if stage_name and stage_name != "Overall":
                if t.get("group") and t["group"] != stage_name:
                    continue
            team_stats[abbr] = {
                "abbr": abbr,
                "name": t.get("name", abbr),
                "matchW": 0, "matchL": 0,
                "mapW": 0, "mapL": 0,
                "rndW": 0, "rndL": 0,
            }

        for m in match_list:
            t1, t2 = m.get("team1", ""), m.get("team2", "")
            s1, s2 = m.get("score1", 0), m.get("score2", 0)

            for t in [t1, t2]:
                if t and t not in team_stats:
                    team_stats[t] = {
                        "abbr": t, "name": t,
                        "matchW": 0, "matchL": 0,
                        "mapW": 0, "mapL": 0,
                        "rndW": 0, "rndL": 0,
                    }

            if not t1 or not t2:
                continue

            if s1 > s2:
                team_stats[t1]["matchW"] += 1
                team_stats[t2]["matchL"] += 1
            elif s2 > s1:
                team_stats[t2]["matchW"] += 1
                team_stats[t1]["matchL"] += 1

            for mp in m.get("maps", []):
                ms1, ms2 = mp.get("score1", 0), mp.get("score2", 0)
                if ms1 > ms2:
                    team_stats[t1]["mapW"] += 1
                    team_stats[t2]["mapL"] += 1
                elif ms2 > ms1:
                    team_stats[t2]["mapW"] += 1
                    team_stats[t1]["mapL"] += 1

                team_stats[t1]["rndW"] += ms1
                team_stats[t1]["rndL"] += ms2
                team_stats[t2]["rndW"] += ms2
                team_stats[t2]["rndL"] += ms1

        rows = list(team_stats.values())
        rows.sort(key=lambda t: (
            t["matchW"],
            t["mapW"] - t["mapL"],
            t["rndW"] - t["rndL"],
        ), reverse=True)

        for i, row in enumerate(rows):
            row["rank"] = i + 1
            row["mapDiff"] = row["mapW"] - row["mapL"]
            row["rndDiff"] = row["rndW"] - row["rndL"]

        return rows

    result = {}
    result["Overall"] = compute_standings(completed, "Overall")
    for stage, stage_matches in sorted(stages.items()):
        result[stage] = compute_standings(stage_matches, stage)

    out_path = os.path.join(event_dir, "standings.json")
    with open(out_path, "w") as fp:
        json.dump(result, fp, indent=2)

    print(f"  standings.json: {len(result)} stages")


def build_event(region, season):
    """Combine + regenerate all derived files for one season."""
    sd = season_dir(region, season)
    print(f"\nEvent: {region}/{season}  ({sd})")

    matches = combine_matches(region, season)
    generate_player_stats(sd, matches)
    generate_team_map_stats(sd, matches)
    generate_standings(sd, matches)
    generate_agent_stats(sd, matches)
    return matches


def main():
    args = sys.argv[1:]
    if len(args) == 2:
        build_event(args[0], args[1])
        return

    events = discover_events()
    if not events:
        print(f"No events found under {EVENTS_DIR}")
        sys.exit(1)
    print(f"Found {len(events)} event(s): {', '.join(f'{r}/{s}' for r, s in events)}")
    for region, season in events:
        build_event(region, season)
    print(f"\nDone! Processed {len(events)} event(s)")


if __name__ == "__main__":
    main()