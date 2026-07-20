#!/usr/bin/env python3
"""
Stitch round ranges from two or more tracker matches into one map.

Use this when a map got remade partway and the game ended up split across two
tracker records, e.g. rounds 1-15 played out in the first record and the rest
in the second. Every stat is recomputed from the per-round data, so the
scoreboard reflects only the rounds you picked -- the match-level totals the
API hands back are ignored.

Usage:
    python stitch.py <url> 1-15 <url> 16-
    python stitch.py <url> 1-15 <url> 16-24 --json stitched.json

Round specs are 1-based and refer to round numbers *within that link*:
    1-15        rounds 1 through 15
    16-         round 16 to the end
    1-9,11-15   skip round 10
    all         every round
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()

MATCH_BASE = "https://api.henrikdev.xyz/valorant/v2/match"
API_KEY = os.getenv("HENRIK_API_KEY")
PUUID_MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "puuid_map.json")

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

TRADE_WINDOW_MS = 5000


def die(msg):
    print(msg)
    sys.exit(1)


def load_puuid_map():
    if os.path.exists(PUUID_MAP_FILE):
        with open(PUUID_MAP_FILE) as f:
            return json.load(f)
    return {}


def fetch_match(match_id):
    resp = requests.get(
        f"{MATCH_BASE}/{match_id}",
        headers={"Authorization": API_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != 200:
        raise ValueError(f"API status {data.get('status')}: {data.get('errors', '')}")
    return data["data"]


def parse_spec(spec, n_rounds, label):
    """'1-15' / '16-' / '1-9,11-15' / 'all' -> sorted list of 0-based indices."""
    if spec.strip().lower() == "all":
        return list(range(n_rounds))
    picked = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d*)", part)
        if m:
            lo = int(m.group(1))
            hi = int(m.group(2)) if m.group(2) else n_rounds
        elif part.isdigit():
            lo = hi = int(part)
        else:
            die(f"{label}: couldn't read round spec {part!r}.")
        if lo < 1 or hi < lo:
            die(f"{label}: round range {part!r} doesn't make sense.")
        if hi > n_rounds:
            die(f"{label}: asked for round {hi} but that match only has {n_rounds}.")
        picked.extend(range(lo - 1, hi))
    if not picked:
        die(f"{label}: round spec {spec!r} selected nothing.")
    return sorted(set(picked))


# Team colors are per-match. The same team can be Red in one record and Blue in
# the other, so every source after the first gets flipped into the first one's
# frame before anything is summed.
def color_align(ref_source, source):
    ref_red = {p["puuid"] for p in ref_source["players"]["all_players"] if p["team"] == "Red"}
    ref_blue = {p["puuid"] for p in ref_source["players"]["all_players"] if p["team"] == "Blue"}
    red = {p["puuid"] for p in source["players"]["all_players"] if p["team"] == "Red"}
    blue = {p["puuid"] for p in source["players"]["all_players"] if p["team"] == "Blue"}

    same = len(red & ref_red) + len(blue & ref_blue)
    flipped = len(red & ref_blue) + len(blue & ref_red)
    if same >= flipped:
        return {"Red": "Red", "Blue": "Blue"}, max(same, flipped)
    return {"Red": "Blue", "Blue": "Red"}, max(same, flipped)


def translate_colors(obj, cmap):
    """Deep-copy obj, rewriting every team-ish field through cmap."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "team" or k.endswith("_team"):
                out[k] = cmap.get(v, v) if isinstance(v, str) else translate_colors(v, cmap)
            elif k == "winning_team":
                out[k] = cmap.get(v, v)
            else:
                out[k] = translate_colors(v, cmap)
        return out
    if isinstance(obj, list):
        return [translate_colors(v, cmap) for v in obj]
    return obj


# Attacker side is read off the round's own plant event where there is one.
# Rounds with no plant fall back to the side pattern of the match they came
# from, since a remade game restarts its own side rotation.
def attack_side_for(source_rounds, idx):
    rnd = source_rounds[idx]
    pe = rnd.get("plant_events")
    if pe and pe.get("planted_by") and pe["planted_by"].get("team"):
        return pe["planted_by"]["team"]

    first_plant_side, first_plant_idx = None, None
    for i, r in enumerate(source_rounds):
        pe = r.get("plant_events")
        if pe and pe.get("planted_by") and pe["planted_by"].get("team"):
            first_plant_side, first_plant_idx = pe["planted_by"]["team"], i
            break
    if first_plant_side is None:
        return "Red"

    other = "Blue" if first_plant_side == "Red" else "Red"

    def half(n):  # 0-based round -> which side block it belongs to
        if n < 12:
            return 0
        if n < 24:
            return 1
        return 2 + (n - 24) // 2

    return first_plant_side if half(idx) % 2 == half(first_plant_idx) % 2 else other


def accumulate(rounds):
    """Recompute every stat from scratch over the stitched round list."""
    agg = defaultdict(lambda: {
        "rounds": 0, "score": 0, "K": 0, "D": 0, "A": 0, "damage": 0,
        "hs": 0, "bs": 0, "ls": 0, "FK": 0, "FD": 0, "kast": 0,
        "team": None, "name": None,
    })

    for rnd in rounds:
        stats = rnd.get("player_stats", [])
        present = set()

        for ps in stats:
            pu = ps.get("player_puuid")
            if not pu:
                continue
            present.add(pu)
            a = agg[pu]
            a["rounds"] += 1
            a["score"] += ps.get("score", 0)
            a["K"] += ps.get("kills", 0)
            a["damage"] += ps.get("damage", 0)
            a["hs"] += ps.get("headshots", 0)
            a["bs"] += ps.get("bodyshots", 0)
            a["ls"] += ps.get("legshots", 0)
            a["team"] = ps.get("player_team") or a["team"]
            a["name"] = ps.get("player_display_name") or a["name"]

        # One flat timeline of the round's kills, ordered by clock.
        timeline = []
        for ps in stats:
            for ke in ps.get("kill_events", []):
                timeline.append({
                    "time": ke.get("kill_time_in_round", 0),
                    "killer": ke.get("killer_puuid"),
                    "victim": ke.get("victim_puuid"),
                    "assists": [x.get("assistant_puuid") for x in ke.get("assistants", [])],
                })
        timeline.sort(key=lambda x: x["time"])

        killers, deaths, assistants = set(), set(), set()
        for ev in timeline:
            if ev["victim"]:
                deaths.add(ev["victim"])
                agg[ev["victim"]]["D"] += 1
            if ev["killer"]:
                killers.add(ev["killer"])
            for ap in ev["assists"]:
                if ap:
                    assistants.add(ap)
                    agg[ap]["A"] += 1

        if timeline:
            first = timeline[0]
            if first["killer"]:
                agg[first["killer"]]["FK"] += 1
            if first["victim"]:
                agg[first["victim"]]["FD"] += 1

        traded = set()
        for i, k in enumerate(timeline):
            for j in range(i + 1, len(timeline)):
                if timeline[j]["time"] - k["time"] > TRADE_WINDOW_MS:
                    break
                if timeline[j]["victim"] == k["killer"]:
                    traded.add(k["victim"])
                    break

        for pu in present:
            if pu in killers or pu in assistants or pu not in deaths or pu in traded:
                agg[pu]["kast"] += 1

    return agg


# Mirrors compute_rating in match.py -- keep the two in step.
def compute_rating(k, d, a, kast, adr, rounds):
    if rounds == 0:
        return 0.0
    kpr, dpr, apr = k / rounds, d / rounds, a / rounds
    return round(
        0.898 * kpr + 0.228 * apr - 0.434 * dpr
        + 0.0025 * (adr - 140 * kpr)
        + 0.313 * kast + 0.295, 3)


def build_players(agg, agents, puuid_map):
    out = {"Red": [], "Blue": []}
    for pu, a in agg.items():
        r = a["rounds"]
        shots = a["hs"] + a["bs"] + a["ls"]
        kast = a["kast"] / r if r else 0
        adr = a["damage"] / r if r else 0
        out.setdefault(a["team"], []).append({
            "puuid": pu,
            "riot_name": a["name"] or "?",
            "pcmt_name": puuid_map.get(pu),
            "team_color": a["team"],
            "agent": agents.get(pu, "?"),
            "rounds": r,
            "R1.0": compute_rating(a["K"], a["D"], a["A"], kast, adr, r),
            "ACS": round(a["score"] / r) if r else 0,
            "K": a["K"], "D": a["D"], "A": a["A"],
            "KAST": round(kast, 3),
            "ADR": round(adr, 1),
            "HS_pct": round(a["hs"] / shots, 3) if shots else 0,
            "FK": a["FK"], "FD": a["FD"],
        })
    for side in out:
        out[side].sort(key=lambda p: p["R1.0"], reverse=True)
    return out


def print_scoreboard(side, players, score, total_rounds):
    print(f"\n{side}  ({score})")
    print(f"  {'Player':<22}{'Agent':<12}{'R':>6}{'ACS':>6}{'K':>4}{'D':>4}{'A':>4}"
          f"{'KAST':>7}{'ADR':>7}{'HS%':>7}{'FK':>4}{'FD':>4}")
    for p in players:
        name = p["pcmt_name"] or p["riot_name"]
        if p["rounds"] != total_rounds:
            name += f" ({p['rounds']}r)"
        print(f"  {name:<22}{p['agent']:<12}{p['R1.0']:>6}{p['ACS']:>6}"
              f"{p['K']:>4}{p['D']:>4}{p['A']:>4}"
              f"{p['KAST'] * 100:>6.0f}%{p['ADR']:>7}{p['HS_pct'] * 100:>6.0f}%"
              f"{p['FK']:>4}{p['FD']:>4}")


def main():
    ap = argparse.ArgumentParser(
        description="Stitch round ranges from two or more tracker matches into one map.",
        epilog="example: python stitch.py <url> 1-15 <url> 16-")
    ap.add_argument("pairs", nargs="+", metavar="URL SPEC",
                    help="tracker url followed by its round spec, repeated")
    ap.add_argument("--json", metavar="FILE",
                    help="also write the stitched map as JSON")
    args = ap.parse_args()

    if not API_KEY:
        die("No HENRIK_API_KEY set. Add it to your .env.")
    if len(args.pairs) < 2 or len(args.pairs) % 2:
        die("Give a round spec after each url, e.g.\n"
            "  python stitch.py <url> 1-15 <url> 16-")

    pairs = list(zip(args.pairs[::2], args.pairs[1::2]))

    sources = []
    for i, (url, spec) in enumerate(pairs):
        label = f"Link {i + 1}"
        m = UUID_RE.search(url)
        if not m:
            die(f"{label}: couldn't find a match id in that URL.")
        try:
            data = fetch_match(m.group(0))
        except Exception as e:
            die(f"{label}: {e}")
        sources.append({"label": label, "spec": spec, "data": data,
                        "rounds": data.get("rounds", [])})

    # Sanity: same map, and enough shared players to trust the stitch.
    map_names = {s["data"]["metadata"]["map"] for s in sources}
    if len(map_names) > 1:
        print(f"WARNING: links are on different maps ({', '.join(sorted(map_names))}). "
              "Stitching anyway.\n")

    ref = sources[0]["data"]
    stitched_rounds = []
    provenance = []

    for s in sources:
        idxs = parse_spec(s["spec"], len(s["rounds"]), s["label"])
        if s["data"] is ref:
            cmap, overlap = {"Red": "Red", "Blue": "Blue"}, 10
        else:
            cmap, overlap = color_align(ref, s["data"])
            if overlap < 6:
                print(f"WARNING: {s['label']} only shares {overlap}/10 players with "
                      f"{sources[0]['label']}. Side alignment may be wrong.\n")
        s["cmap"] = cmap

        for idx in idxs:
            rnd = translate_colors(s["rounds"][idx], cmap)
            atk = cmap.get(attack_side_for(s["rounds"], idx))
            stitched_rounds.append(rnd)
            provenance.append({
                "from": s["label"], "source_round": idx + 1,
                "winner": rnd.get("winning_team"),
                "attackers": atk,
                "end_type": rnd.get("end_type", ""),
            })

    total = len(stitched_rounds)
    if not total:
        die("No rounds selected.")

    red_score = sum(1 for r in stitched_rounds if r.get("winning_team") == "Red")
    blue_score = sum(1 for r in stitched_rounds if r.get("winning_team") == "Blue")

    # Agent per puuid, from whichever source first listed them.
    agents = {}
    for s in sources:
        for p in s["data"]["players"]["all_players"]:
            agents.setdefault(p["puuid"], p.get("character", "?"))

    agg = accumulate(stitched_rounds)
    players = build_players(agg, agents, load_puuid_map())

    print(f"{ref['metadata']['map']} - {total} rounds stitched")
    for s in sources:
        flip = " (colors flipped)" if s.get("cmap", {}).get("Red") == "Blue" else ""
        print(f"  {s['label']}: rounds {s['spec']}{flip}")
    print(f"\nRed {red_score} - {blue_score} Blue")

    print_scoreboard("Red", players.get("Red", []), red_score, total)
    print_scoreboard("Blue", players.get("Blue", []), blue_score, total)

    print("\nRound order")
    for n, pv in enumerate(provenance, 1):
        print(f"  {n:>3}. {pv['from']} r{pv['source_round']:<3} "
              f"{pv['winner'] or '?':<5} {pv['attackers']} attacking   {pv['end_type']}")

    if args.json:
        out = {
            "map_name": ref["metadata"]["map"],
            "rounds_played": total,
            "red_score": red_score,
            "blue_score": blue_score,
            "red_players": players.get("Red", []),
            "blue_players": players.get("Blue", []),
            "raw_rounds": stitched_rounds,
            "stitched_from": provenance,
        }
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
