#!/usr/bin/env python3
"""
Insert a map into an existing match file, for maps the bot can't fetch
(no tracker link). Recomputes the series score, map list and combined stats.

Usage:
    python add_map.py <region> <season> <match-id> --map map2.json --at 2
    python add_map.py emea s2 faze-vs-hot-... --map map2.json --at 2 --build

Map file format (score1/score2 and the team keys follow the match's team1/team2):
{
  "name": "Ascent",
  "score1": 13,
  "score2": 9,
  "rounds": null,
  "stats": {
    "FaZe": [
      {"Player": "Kramvi", "Agent": "Jett", "ACS": 250, "K": 20, "D": 14,
       "A": 5, "KAST": 0.75, "ADR": 160.0, "HS%": 0.21, "FK": 3, "FD": 2}
    ],
    "HOT": []
  }
}
"""
import argparse
import json
import os
import sys

from build import compute_rating
from data_helpers import match_file_path, team_name, load_teams


def die(msg):
    print(msg)
    sys.exit(1)


def map_rounds(detail):
    return detail.get("score1", 0) + detail.get("score2", 0)


def build_map(spec, match):
    """Map file -> a mapDetails entry, filling in the derived per-player fields."""
    rounds = map_rounds(spec)
    if rounds == 0:
        die("Map score is 0-0; set score1 and score2.")

    order = [(match["team1"], match["team1Name"]), (match["team2"], match["team2Name"])]
    given = set(spec.get("stats", {}))
    if given != {match["team1"], match["team2"]}:
        die(f"Map stats keys {sorted(given)} don't match the teams "
            f"{sorted([match['team1'], match['team2']])}.")

    blocks = []
    for abbr, name in order:
        players = []
        for p in spec["stats"][abbr]:
            k, d, a = p["K"], p["D"], p["A"]
            kast, adr = p["KAST"], p["ADR"]
            fk, fd = p.get("FK", 0), p.get("FD", 0)
            players.append({
                "Player": p["Player"],
                "Agent": p.get("Agent", "?"),
                "R1.0": compute_rating(k, d, a, kast, adr, fk, fd, rounds),
                "ACS": round(p["ACS"]),
                "K": k, "D": d, "A": a,
                "PlusMinus": k - d,
                "KAST": round(kast, 3),
                "ADR": round(adr, 1),
                "HS%": round(p["HS%"], 3),
                "FK": fk, "FD": fd,
                "PlusMinus2": fk - fd,
            })
        players.sort(key=lambda x: x["R1.0"], reverse=True)
        blocks.append({"team": abbr, "teamName": name, "players": players})

    return {
        "name": spec["name"],
        "score1": spec["score1"],
        "score2": spec["score2"],
        "rounds": spec.get("rounds"),
        "stats": blocks,
    }


def recompute(match):
    """Series score, map list and combined stats, from mapDetails."""
    details = match["mapDetails"]
    match["maps"] = [{"name": d["name"], "score1": d["score1"], "score2": d["score2"]}
                     for d in details]
    match["score1"] = sum(1 for d in details if d["score1"] > d["score2"])
    match["score2"] = sum(1 for d in details if d["score2"] > d["score1"])

    agg = {}
    for d in details:
        rounds = map_rounds(d)
        for block in d["stats"]:
            for p in block["players"]:
                a = agg.setdefault(p["Player"], {
                    "rounds": 0, "K": 0, "D": 0, "A": 0, "FK": 0, "FD": 0,
                    "ACS_w": 0, "KAST_w": 0, "ADR_w": 0, "HS_w": 0,
                    "team": block["team"], "teamName": block["teamName"],
                })
                a["rounds"] += rounds
                for key in ("K", "D", "A", "FK", "FD"):
                    a[key] += p[key]
                a["ACS_w"] += p["ACS"] * rounds
                a["KAST_w"] += p["KAST"] * rounds
                a["ADR_w"] += p["ADR"] * rounds
                a["HS_w"] += p["HS%"] * rounds

    combined = {}
    for name, a in agg.items():
        r = a["rounds"]
        acs = round(a["ACS_w"] / r) if r else 0
        kast = round(a["KAST_w"] / r, 3) if r else 0
        adr = round(a["ADR_w"] / r, 1) if r else 0
        hs = round(a["HS_w"] / r, 3) if r else 0
        key = (a["team"], a["teamName"])
        combined.setdefault(key, {"team": a["team"], "teamName": a["teamName"], "players": []})
        combined[key]["players"].append({
            "Player": name,
            "R1.0": compute_rating(a["K"], a["D"], a["A"], kast, adr, a["FK"], a["FD"], r),
            "ACS": acs,
            "K": a["K"], "D": a["D"], "A": a["A"],
            "PlusMinus": a["K"] - a["D"],
            "KAST": kast, "ADR": adr, "HS%": hs,
            "FK": a["FK"], "FD": a["FD"],
            "PlusMinus2": a["FK"] - a["FD"],
        })

    match["combinedStats"] = []
    for key in [(match["team1"], match["team1Name"]), (match["team2"], match["team2Name"])]:
        if key in combined:
            block = combined[key]
            block["players"].sort(key=lambda p: p["R1.0"], reverse=True)
            match["combinedStats"].append(block)


def main():
    ap = argparse.ArgumentParser(description="Insert a map into a match file by hand.")
    ap.add_argument("region")
    ap.add_argument("season")
    ap.add_argument("match_id")
    ap.add_argument("--map", required=True, metavar="FILE", help="the map to insert")
    ap.add_argument("--at", type=int, metavar="N", help="map number, 1-based (default: append)")
    ap.add_argument("--replace", action="store_true", help="replace the map at --at instead of inserting")
    ap.add_argument("--build", action="store_true", help="rebuild the event afterwards")
    args = ap.parse_args()

    path = match_file_path(args.region, args.season, args.match_id)
    if not os.path.exists(path):
        die(f"No match file at {path}")
    with open(path, encoding="utf-8") as f:
        match = json.load(f)
    with open(args.map, encoding="utf-8") as f:
        spec = json.load(f)

    detail = build_map(spec, match)
    details = match.setdefault("mapDetails", [])
    at = len(details) if args.at is None else args.at - 1
    if args.replace:
        if not 0 <= at < len(details):
            die(f"--at {args.at} is outside 1..{len(details)}.")
        details[at] = detail
    else:
        if not 0 <= at <= len(details):
            die(f"--at {args.at} is outside 1..{len(details) + 1}.")
        details.insert(at, detail)

    recompute(match)
    match["completed"] = True

    with open(path, "w", encoding="utf-8") as f:
        json.dump(match, f, indent=2)

    print(f"{match['team1Name']} {match['score1']} - {match['score2']} {match['team2Name']}")
    for i, d in enumerate(match["maps"], 1):
        mark = "  <- added" if i == at + 1 else ""
        print(f"  map {i}: {d['name']:9} {d['score1']}-{d['score2']}{mark}")
    print(f"\nWrote {path}")

    if args.build:
        from build import build_event
        build_event(args.region, args.season)


if __name__ == "__main__":
    main()
