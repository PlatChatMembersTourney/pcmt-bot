#!/usr/bin/env python3
"""
Merge a half's scoreboard into a map that already holds the other half.

Use this when a map was remade and only one half has a tracker, so the bot
entered that half as the whole map. Totals add up, per-round stats (ACS, ADR,
KAST, HS%) are re-weighted by the rounds each player actually played.

Usage:
    python merge_half.py <region> <season> <match-id> --map 2 --half half1.json
    python merge_half.py emea s2 faze-vs-hot-... --map 2 --half half1.json --build

Half file format (score1/score2 and team keys follow the match's team1/team2).
"rounds" per player is optional, for anyone who didn't play the whole half:
{
  "score1": 8,
  "score2": 4,
  "stats": {
    "FaZe": [
      {"Player": "Kramvi", "Agent": "Jett", "ACS": 250, "K": 12, "D": 7, "A": 3,
       "KAST": 0.75, "ADR": 160.0, "HS%": 0.21, "FK": 2, "FD": 1}
    ],
    "HOT": []
  },
  "playedRounds": {"someSub": 5},
  "existingRounds": {"otherSub": 7},
  "rounds": [{"round": 1, "winner": 1, "side": "atk", "endType": "Eliminated"}]
}

"rounds" is optional. Give it and the map keeps a full timeline (this half's
rounds first, then the half already in the file, renumbered). Leave it out and
the timeline is dropped, since a half-timeline would be misleading.

"playedRounds" is for the half being merged in, "existingRounds" for the half
already in the file. Anyone not named is assumed to have played the full half.
"""
import argparse
import json
import os
import sys

from add_map import recompute, map_rounds
from build import compute_rating
from data_helpers import match_file_path

AVERAGED = ("ACS", "KAST", "ADR", "HS%")
TOTALS = ("K", "D", "A", "FK", "FD")


def die(msg):
    print(msg)
    sys.exit(1)


def blend(parts):
    """[(stats, rounds)] for one player -> one line. Totals add, per-round stats
    weight by the rounds behind them. A stat missing from a part is unknown, so
    that part is left out of its average rather than counted as zero."""
    total = sum(r for _, r in parts)
    out = {}
    for key in TOTALS:
        out[key] = sum(p.get(key, 0) for p, _ in parts)
    for key in AVERAGED:
        known = [(p[key], r) for p, r in parts if key in p]
        rounds = sum(r for _, r in known)
        out[key] = sum(v * r for v, r in known) / rounds if rounds else 0
    out["Player"] = next(p["Player"] for p, _ in parts if p.get("Player"))
    out["Agent"] = next((p["Agent"] for p, _ in parts if p.get("Agent")), "?")
    out["ACS"] = round(out["ACS"])
    out["KAST"] = round(out["KAST"], 3)
    out["ADR"] = round(out["ADR"], 1)
    out["HS%"] = round(out["HS%"], 3)
    out["PlusMinus"] = out["K"] - out["D"]
    out["PlusMinus2"] = out["FK"] - out["FD"]
    out["R1.0"] = compute_rating(out["K"], out["D"], out["A"], out["KAST"],
                                 out["ADR"], out["FK"], out["FD"], total)
    return out


def main():
    ap = argparse.ArgumentParser(description="Merge a half's stats into an existing map.")
    ap.add_argument("region")
    ap.add_argument("season")
    ap.add_argument("match_id")
    ap.add_argument("--map", type=int, required=True, metavar="N", help="map number, 1-based")
    ap.add_argument("--half", required=True, metavar="FILE", help="the half to merge in")
    ap.add_argument("--keep-rounds", action="store_true",
                    help="keep the existing round timeline even though it only covers one half")
    ap.add_argument("--keep-score", action="store_true",
                    help="don't add the half's score (for rounds that were replayed)")
    ap.add_argument("--after", action="store_true",
                    help="the merged half was played second (default: first)")
    ap.add_argument("--build", action="store_true", help="rebuild the event afterwards")
    args = ap.parse_args()

    path = match_file_path(args.region, args.season, args.match_id)
    if not os.path.exists(path):
        die(f"No match file at {path}")
    with open(path, encoding="utf-8") as f:
        match = json.load(f)
    with open(args.half, encoding="utf-8") as f:
        half = json.load(f)

    details = match.get("mapDetails", [])
    if not 1 <= args.map <= len(details):
        die(f"--map {args.map} is outside 1..{len(details)}.")
    detail = details[args.map - 1]

    teams = {match["team1"], match["team2"]}
    if set(half.get("stats", {})) != teams:
        die(f"Half stats keys {sorted(half.get('stats', {}))} don't match the teams {sorted(teams)}.")

    played = half.get("playedRounds", {})
    already = half.get("existingRounds", {})
    existing_rounds = map_rounds(detail)
    incoming_rounds = half["score1"] + half["score2"]
    if incoming_rounds == 0:
        die("Half score is 0-0; set score1 and score2.")

    added, merged = [], []
    for block in detail["stats"]:
        incoming = {p["Player"]: p for p in half["stats"][block["team"]]}
        by_name = {p["Player"]: p for p in block["players"]}

        for name, p in by_name.items():
            mine = already.get(name, existing_rounds)
            if name in incoming:
                theirs = played.get(name, incoming_rounds)
                by_name[name] = blend([(p, mine), (incoming.pop(name), theirs)])
                merged.append(name)
            elif mine != existing_rounds:  # Played only part of the half already there
                by_name[name] = blend([(p, mine)])

        # Anyone who only played the half being merged in
        for name, p in incoming.items():
            theirs = played.get(name, incoming_rounds)
            by_name[name] = blend([(p, theirs)])
            added.append(name)

        block["players"] = sorted(by_name.values(), key=lambda x: x["R1.0"], reverse=True)

    if not args.keep_score:
        detail["score1"] += half["score1"]
        detail["score2"] += half["score2"]

    # Timeline: only worth keeping if both halves have one
    incoming_timeline = half.get("rounds")
    existing_timeline = detail.get("rounds")
    if incoming_timeline and existing_timeline:
        order = ([existing_timeline, incoming_timeline] if args.after
                 else [incoming_timeline, existing_timeline])
        merged_timeline = [dict(r) for part in order for r in part]
        for n, rnd in enumerate(merged_timeline, 1):
            rnd["round"] = n
        detail["rounds"] = merged_timeline
        total = detail["score1"] + detail["score2"]
        if len(merged_timeline) != total:
            print(f"note: timeline has {len(merged_timeline)} rounds but the map score adds to {total}")
    elif incoming_timeline and not existing_timeline:
        detail["rounds"] = [dict(r, round=n) for n, r in enumerate(incoming_timeline, 1)]
    elif not args.keep_rounds:
        detail["rounds"] = None

    recompute(match)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(match, f, indent=2)

    print(f"map {args.map} ({detail['name']}): {existing_rounds} + {incoming_rounds} rounds "
          f"-> {detail['score1']}-{detail['score2']}")
    print(f"  merged {len(merged)} player(s)" + (f", added {', '.join(added)}" if added else ""))
    print(f"{match['team1Name']} {match['score1']} - {match['score2']} {match['team2Name']}")
    for i, m in enumerate(match["maps"], 1):
        print(f"  map {i}: {m['name']:9} {m['score1']}-{m['score2']}")
    print(f"\nWrote {path}")

    if args.build:
        from build import build_event
        build_event(args.region, args.season)


if __name__ == "__main__":
    main()
