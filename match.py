import json
import os
import re
from collections import defaultdict

import discord
from dotenv import load_dotenv

from data_helpers import (
    BASE_DIR, list_regions, list_seasons, load_teams, load_matches,
    match_file_path, matches_dir,
)
from build import build_event

load_dotenv()

HENRIK_API_BASE = "https://api.henrikdev.xyz/valorant/v2/match"
PUUID_MAP_FILE = os.path.join(BASE_DIR, "puuid_map.json")


def get_api_key():
    return os.getenv("HENRIK_API_KEY")


# puuid -> pcmt player name, learned over time
def load_puuid_map():
    if os.path.exists(PUUID_MAP_FILE):
        with open(PUUID_MAP_FILE) as f:
            return json.load(f)
    return {}


def save_puuid_map(pmap):
    with open(PUUID_MAP_FILE, "w") as f:
        json.dump(pmap, f, indent=2)


def normalize(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


MAPS = [
    "Abyss", "Ascent", "Bind", "Breeze", "Corrode", "Fracture", "Haven",
    "Icebox", "Lotus", "Pearl", "Split", "Summit", "Sunset",
]

# Each step is (action, slot). slot resolves to team1 / team2 / decider.
VETO_TEMPLATES = {
    "BO1": [("bans", "t1"), ("bans", "t2"), ("bans", "t1"), ("bans", "t2"),
            ("bans", "t1"), ("bans", "t2"), ("remains", "decider")],
    "BO3": [("bans", "t1"), ("bans", "t2"), ("picks", "t1"), ("picks", "t2"),
            ("bans", "t1"), ("bans", "t2"), ("remains", "decider")],
    "BO5": [("bans", "t1"), ("bans", "t2"), ("picks", "t1"), ("picks", "t2"),
            ("picks", "t1"), ("picks", "t2"), ("remains", "decider")],
}


def veto_template(best_of):
    return VETO_TEMPLATES.get(f"BO{best_of}", VETO_TEMPLATES["BO3"])


def step_label(action, slot, first, second):
    if slot == "decider":
        return "Decider (map remains)"
    team = first if slot == "t1" else second
    return f"{team} {action}"


def build_veto_string(template, picks, first, second):
    parts = []
    for (action, slot), mp in zip(template, picks):
        if slot == "decider" or action == "remains":
            parts.append(f"{mp} remains")
        else:
            team = first if slot == "t1" else second
            parts.append(f"{team} {action} {mp}")
    return ", ".join(parts)


# API fetch + parse
def extract_match_id(url):
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", url, re.I)
    return m.group(0) if m else None


def fetch_match(match_id, api_key):
    import requests
    resp = requests.get(
        f"{HENRIK_API_BASE}/{match_id}",
        headers={"Authorization": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != 200:
        raise ValueError(f"API status {data.get('status')}: {data.get('errors', '')}")
    return data["data"]


def compute_fk_fd(rounds_data):
    fk, fd = defaultdict(int), defaultdict(int)
    for rnd in rounds_data:
        best_time = float("inf")
        killer = victim = None
        for ps in rnd.get("player_stats", []):
            for ke in ps.get("kill_events", []):
                t = ke.get("kill_time_in_round", float("inf"))
                if t < best_time:
                    best_time = t
                    killer = ke.get("killer_puuid")
                    victim = ke.get("victim_puuid")
        if killer:
            fk[killer] += 1
        if victim:
            fd[victim] += 1
    return dict(fk), dict(fd)


def compute_kast(rounds_data, puuids):
    kast_rounds = defaultdict(int)
    total = len(rounds_data)
    for rnd in rounds_data:
        killers, deaths, assistants, timeline = set(), set(), set(), []
        for ps in rnd.get("player_stats", []):
            pu = ps.get("player_puuid", "")
            if ps.get("kills", 0) > 0:
                killers.add(pu)
            for ke in ps.get("kill_events", []):
                v = ke.get("victim_puuid")
                if v:
                    deaths.add(v)
                for a in ke.get("assistants", []):
                    ap = a.get("assistant_puuid")
                    if ap:
                        assistants.add(ap)
                timeline.append({
                    "time": ke.get("kill_time_in_round", 0),
                    "killer": ke.get("killer_puuid"),
                    "victim": ke.get("victim_puuid"),
                })
        timeline.sort(key=lambda x: x["time"])
        traded = set()
        for i, k in enumerate(timeline):
            for j in range(i + 1, len(timeline)):
                if timeline[j]["time"] - k["time"] > 5000:
                    break
                if timeline[j]["victim"] == k["killer"]:
                    traded.add(k["victim"])
                    break
        for pu in puuids:
            if pu in killers or pu in assistants or pu not in deaths or pu in traded:
                kast_rounds[pu] += 1
    return {pu: kast_rounds[pu] / total if total else 0 for pu in puuids}


def compute_rating(k, d, a, kast, adr, fk, fd, total_rounds):
    if total_rounds == 0:
        return 0.0
    kpr, dpr, apr = k / total_rounds, d / total_rounds, a / total_rounds
    return round(
        0.898 * kpr + 0.228 * apr - 0.434 * dpr
        + 0.0025 * (adr - 140 * kpr) + 0.434 * (1 - dpr)
        + 0.313 * kast + 0.175, 3)


def parse_api_match(data):
    meta = data["metadata"]
    rp = meta["rounds_played"]
    all_players = data["players"]["all_players"]
    rounds_data = data.get("rounds", [])
    puuids = [p["puuid"] for p in all_players]
    fk_map, fd_map = compute_fk_fd(rounds_data)
    kast_map = compute_kast(rounds_data, puuids)

    parsed = []
    for p in all_players:
        s = p["stats"]
        pu = p["puuid"]
        shots = s["headshots"] + s["bodyshots"] + s["legshots"]
        parsed.append({
            "puuid": pu,
            "riot_name": p["name"],
            "riot_tag": p["tag"],
            "team_color": p["team"],
            "agent": p["character"],
            "ACS": round(s["score"] / rp) if rp else 0,
            "K": s["kills"], "D": s["deaths"], "A": s["assists"],
            "KAST": round(kast_map.get(pu, 0), 3),
            "ADR": round(p["damage_made"] / rp, 1) if rp else 0,
            "HS_pct": round(s["headshots"] / shots, 3) if shots else 0,
            "FK": fk_map.get(pu, 0), "FD": fd_map.get(pu, 0),
        })

    return {
        "map_name": meta["map"],
        "rounds_played": rp,
        "red_score": data["teams"]["red"]["rounds_won"],
        "blue_score": data["teams"]["blue"]["rounds_won"],
        "red_players": [p for p in parsed if p["team_color"] == "Red"],
        "blue_players": [p for p in parsed if p["team_color"] == "Blue"],
        "raw_rounds": rounds_data,
    }


def build_round_timeline(raw_rounds, t1_color):
    if not raw_rounds:
        return []
    atk_start = None
    for rnd in raw_rounds:
        pe = rnd.get("plant_events")
        if pe and pe.get("planted_by"):
            atk_start = pe["planted_by"].get("team")
            break
    if not atk_start:
        atk_start = "Red"

    timeline = []
    for rnd in raw_rounds:
        wc = rnd.get("winning_team")
        if not wc:
            continue
        n = len(timeline) + 1
        winner = 1 if wc.lower() == t1_color else 2
        if n <= 12:
            atk = atk_start
        elif n <= 24:
            atk = "Blue" if atk_start == "Red" else "Red"
        else:
            atk = atk_start if ((n - 25) // 2) % 2 == 0 else ("Blue" if atk_start == "Red" else "Red")
        side = "atk" if wc == atk else "def"
        timeline.append({
            "round": n, "winner": winner, "side": side,
            "endType": rnd.get("end_type", ""),
        })
    return timeline


# Auto-resolution
def detect_color_map(parsed_maps, teams, t1, t2):
    """For each map, decide which in-game color is team1 vs team2 by roster overlap.
    Returns (color_map, low_confidence_maps)."""
    r1 = {normalize(p) for p in teams.get(t1, {}).get("players", [])}
    r2 = {normalize(p) for p in teams.get(t2, {}).get("players", [])}
    color_map, low_conf = {}, []
    for i, api in enumerate(parsed_maps):
        red = {normalize(p["riot_name"]) for p in api["red_players"]}
        blue = {normalize(p["riot_name"]) for p in api["blue_players"]}
        red_t1 = len(red & r1) + len(blue & r2)
        red_t2 = len(red & r2) + len(blue & r1)
        if red_t1 >= red_t2:
            color_map[i] = {"red": t1, "blue": t2}
        else:
            color_map[i] = {"red": t2, "blue": t1}
        if max(red_t1, red_t2) < 2:
            low_conf.append(i)
    return color_map, low_conf


def auto_map_players(parsed_maps, color_map, teams, puuid_map):
    """Resolve each unique puuid to a PCMT name. Returns (mapping, unresolved)."""
    rosters = {abbr: {normalize(p): p for p in t.get("players", [])} for abbr, t in teams.items()}
    mapping, unresolved = {}, []
    seen = {}
    for i, api in enumerate(parsed_maps):
        cmap = color_map[i]
        for color in ("red", "blue"):
            assigned = cmap[color]
            for p in api[f"{color}_players"]:
                pu = p["puuid"]
                if pu in seen:
                    continue
                seen[pu] = True
                if pu in puuid_map:
                    mapping[pu] = puuid_map[pu]
                    continue
                roster = rosters.get(assigned, {})
                key = normalize(p["riot_name"])
                if key in roster:
                    mapping[pu] = roster[key]
                else:
                    mapping[pu] = p["riot_name"]
                    unresolved.append(f"{p['riot_name']}#{p['riot_tag']} ({assigned})")
    return mapping, unresolved


# Assembly
def resolve_map(api, t1, t1n, t2, t2n, cmap, player_mapping):
    t1_color = "red" if cmap["red"] == t1 else "blue"
    t2_color = "blue" if t1_color == "red" else "red"
    rounds = build_round_timeline(api.get("raw_rounds", []), t1_color)

    def team_block(players, abbr, name):
        out = []
        for p in players:
            rating = compute_rating(p["K"], p["D"], p["A"], p["KAST"], p["ADR"],
                                    p["FK"], p["FD"], api["rounds_played"])
            out.append({
                "Player": player_mapping.get(p["puuid"], p["riot_name"]),
                "Agent": p["agent"], "R1.0": rating, "ACS": p["ACS"],
                "K": p["K"], "D": p["D"], "A": p["A"], "PlusMinus": p["K"] - p["D"],
                "KAST": p["KAST"], "ADR": p["ADR"], "HS%": p["HS_pct"],
                "FK": p["FK"], "FD": p["FD"], "PlusMinus2": p["FK"] - p["FD"],
            })
        return {"team": abbr, "teamName": name, "players": out}

    t1b = team_block(api[f"{t1_color}_players"], t1, t1n)
    t2b = team_block(api[f"{t2_color}_players"], t2, t2n)
    return {
        "name": api["map_name"],
        "score1": api[f"{t1_color}_score"],
        "score2": api[f"{t2_color}_score"],
        "rounds": rounds,
        "stats": [t1b, t2b],
    }


def build_match_json(skeleton, parsed_maps, color_map, player_mapping, veto, stream):
    t1, t2 = skeleton["team1"], skeleton["team2"]
    t1n, t2n = skeleton["team1Name"], skeleton["team2Name"]

    resolved = [resolve_map(api, t1, t1n, t2, t2n, color_map[i], player_mapping)
                for i, api in enumerate(parsed_maps)]

    t1_wins = sum(1 for rm in resolved if rm["score1"] > rm["score2"])
    t2_wins = sum(1 for rm in resolved if rm["score2"] > rm["score1"])

    agg = {}
    for i, rm in enumerate(resolved):
        rounds = parsed_maps[i]["rounds_played"]
        for block in rm["stats"]:
            for p in block["players"]:
                name = p["Player"]
                a = agg.setdefault(name, {
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
        rating = compute_rating(a["K"], a["D"], a["A"], kast, adr, a["FK"], a["FD"], r)
        key = (a["team"], a["teamName"])
        combined.setdefault(key, {"team": a["team"], "teamName": a["teamName"], "players": []})
        combined[key]["players"].append({
            "Player": name, "R1.0": rating, "ACS": acs,
            "K": a["K"], "D": a["D"], "A": a["A"], "PlusMinus": a["K"] - a["D"],
            "KAST": kast, "ADR": adr, "HS%": hs,
            "FK": a["FK"], "FD": a["FD"], "PlusMinus2": a["FK"] - a["FD"],
        })

    combined_stats = []
    for key in [(t1, t1n), (t2, t2n)]:
        if key in combined:
            combined_stats.append(combined[key])
    for cs in combined_stats:
        cs["players"].sort(key=lambda p: p["R1.0"], reverse=True)
    for rm in resolved:
        for ts in rm["stats"]:
            ts["players"].sort(key=lambda p: p["R1.0"], reverse=True)

    result = {
        "id": skeleton["id"],
        "team1": t1, "team2": t2, "team1Name": t1n, "team2Name": t2n,
        "score1": t1_wins, "score2": t2_wins,
        "completed": True,
        "bestOf": skeleton.get("bestOf", len(resolved)),
        "date": skeleton.get("date", ""),
        "stage": skeleton.get("stage", ""),
        "veto": veto,
        "maps": [{"name": rm["name"], "score1": rm["score1"], "score2": rm["score2"]} for rm in resolved],
        "combinedStats": combined_stats,
        "mapDetails": resolved,
    }
    if stream:
        result["streamLink"] = stream
    return result


# Discord UI
class RegionSelect(discord.ui.Select):
    def __init__(self, regions):
        options = [discord.SelectOption(label=r.upper(), value=r) for r in regions][:25]
        super().__init__(placeholder="Select region", options=options)

    async def callback(self, interaction):
        self.view.region = self.values[0]
        self.view.rebuild()
        await interaction.response.edit_message(content=self.view.prompt(), view=self.view)


class SeasonSelect(discord.ui.Select):
    def __init__(self, seasons):
        options = [discord.SelectOption(label=s, value=s) for s in seasons][:25]
        super().__init__(placeholder="Select season", options=options)

    async def callback(self, interaction):
        self.view.season = self.values[0]
        self.view.rebuild()
        await interaction.response.edit_message(content=self.view.prompt(), view=self.view)


class MatchSelect(discord.ui.Select):
    def __init__(self, upcoming):
        options = []
        for m in upcoming[:25]:
            label = f"{m['team1']} vs {m['team2']} - {m.get('stage', '')}"[:90]
            desc = m.get("date", "")[:10]
            options.append(discord.SelectOption(label=label, value=m["id"], description=desc))
        super().__init__(placeholder="Select the game to fill in", options=options)

    async def callback(self, interaction):
        self.view.skeleton = self.view.upcoming_by_id[self.values[0]]
        veto_view = VetoView(self.view.author_id, self.view.region,
                             self.view.season, self.view.skeleton)
        await interaction.response.edit_message(content=veto_view.prompt(), view=veto_view)


class LinksModal(discord.ui.Modal, title="Tracker links"):
    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view
        self.links = discord.ui.TextInput(
            label="Tracker.gg links (one per line)",
            style=discord.TextStyle.paragraph,
            placeholder="https://tracker.gg/valorant/match/...\nhttps://...",
            required=True,
        )
        self.stream = discord.ui.TextInput(
            label="Stream VOD URL (optional)", required=False,
        )
        self.add_item(self.links)
        self.add_item(self.stream)

    async def on_submit(self, interaction):
        v = self.parent_view
        await interaction.response.defer(ephemeral=True, thinking=True)

        api_key = get_api_key()
        if not api_key:
            await interaction.followup.send(
                "No HENRIK_API_KEY set. Add it to your .env and restart the bot.",
                ephemeral=True)
            return

        urls = [ln.strip() for ln in str(self.links.value).splitlines() if ln.strip()]
        if not urls:
            await interaction.followup.send("No links found.", ephemeral=True)
            return

        parsed_maps = []
        for i, url in enumerate(urls):
            mid = extract_match_id(url)
            if not mid:
                await interaction.followup.send(f"Map {i+1}: couldn't find a match id in that URL.", ephemeral=True)
                return
            try:
                parsed_maps.append(parse_api_match(fetch_match(mid, api_key)))
            except Exception as e:
                await interaction.followup.send(f"Map {i+1}: {e}", ephemeral=True)
                return

        teams = load_teams(v.region, v.season)
        t1, t2 = v.skeleton["team1"], v.skeleton["team2"]
        color_map, low_conf = detect_color_map(parsed_maps, teams, t1, t2)

        puuid_map = load_puuid_map()
        player_mapping, unresolved = auto_map_players(parsed_maps, color_map, teams, puuid_map)

        match = build_match_json(
            v.skeleton, parsed_maps, color_map, player_mapping,
            getattr(v, "veto_string", ""), str(self.stream.value).strip(),
        )

        os.makedirs(matches_dir(v.region, v.season), exist_ok=True)
        with open(match_file_path(v.region, v.season, match["id"]), "w") as f:
            json.dump(match, f, indent=2)

        # Learn puuid -> name for confidently resolved players (not the fallbacks)
        unresolved_names = {u.split("#")[0] for u in unresolved}
        for pu, name in player_mapping.items():
            if name not in unresolved_names:
                puuid_map[pu] = name
        save_puuid_map(puuid_map)

        build_note = ""
        try:
            build_event(v.region, v.season)
        except Exception as e:
            build_note = f"\nBuild failed: {e}"
            print(f"build failed for {v.region}/{v.season}: {e}")

        lines = [
            f"Saved **{match['team1Name']} {match['score1']} - {match['score2']} {match['team2Name']}**",
            f"{match['stage']} | {len(parsed_maps)} map(s) | id `{match['id']}`",
        ]
        if low_conf:
            maps_txt = ", ".join(parsed_maps[i]["map_name"] for i in low_conf)
            lines.append(f"Low-confidence team/color detection on: {maps_txt}. Double-check those.")
        if unresolved:
            lines.append("Couldn't match (kept Riot name, will show as sub): " + ", ".join(unresolved))
        lines.append(build_note.strip() or "Build ran.")

        await interaction.followup.send("\n".join(lines), ephemeral=True)


class TeamFirstSelect(discord.ui.Select):
    def __init__(self, t1, t2):
        options = [discord.SelectOption(label=t1, value=t1),
                   discord.SelectOption(label=t2, value=t2)]
        super().__init__(placeholder="Who bans/picks first?", options=options)

    async def callback(self, interaction):
        v = self.view
        v.first_team = self.values[0]
        v.second_team = v.skeleton["team2"] if v.first_team == v.skeleton["team1"] else v.skeleton["team1"]
        v.rebuild()
        await interaction.response.edit_message(content=v.prompt(), view=v)


class VetoMapSelect(discord.ui.Select):
    def __init__(self, label, available):
        options = [discord.SelectOption(label=m, value=m) for m in available][:25]
        super().__init__(placeholder=label, options=options)

    async def callback(self, interaction):
        self.view.picks.append(self.values[0])
        self.view.step += 1
        self.view.rebuild()
        if self.view.step >= len(self.view.template):
            await interaction.response.edit_message(content=self.view.summary(), view=self.view)
        else:
            await interaction.response.edit_message(content=self.view.prompt(), view=self.view)


class SkipVetoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Skip veto", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction):
        self.view.picks = []
        self.view.veto_string = ""
        await interaction.response.send_modal(LinksModal(self.view))


class EnterLinksButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Enter tracker links", style=discord.ButtonStyle.primary)

    async def callback(self, interaction):
        await interaction.response.send_modal(LinksModal(self.view))


class RedoVetoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Redo veto", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction):
        self.view.first_team = None
        self.view.second_team = None
        self.view.picks = []
        self.view.step = 0
        self.view.veto_string = ""
        self.view.rebuild()
        await interaction.response.edit_message(content=self.view.prompt(), view=self.view)


class VetoView(discord.ui.View):
    def __init__(self, author_id, region, season, skeleton):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.region = region
        self.season = season
        self.skeleton = skeleton
        self.template = veto_template(skeleton.get("bestOf", 3))
        self.first_team = None
        self.second_team = None
        self.picks = []
        self.step = 0
        self.veto_string = ""
        self.rebuild()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your session.", ephemeral=True)
            return False
        return True

    def prompt(self):
        header = (f"Veto for **{self.skeleton['team1']} vs {self.skeleton['team2']}** "
                  f"(BO{self.skeleton.get('bestOf', 3)})")
        if self.first_team is None:
            return header + "\nWho is team 1 in the veto (bans/picks first)?"
        action, slot = self.template[self.step]
        label = step_label(action, slot, self.first_team, self.second_team)
        done = ", ".join(self.picks) if self.picks else "none yet"
        return (header + "\n"
                f"Step {self.step + 1} of {len(self.template)}: **{label}**\n"
                f"Picked so far: {done}")

    def summary(self):
        self.veto_string = build_veto_string(self.template, self.picks, self.first_team, self.second_team)
        return f"Veto:\n`{self.veto_string}`\n\nLooks right? Enter the tracker links."

    def rebuild(self):
        self.clear_items()
        if self.first_team is None:
            self.add_item(TeamFirstSelect(self.skeleton["team1"], self.skeleton["team2"]))
            self.add_item(SkipVetoButton())
        elif self.step < len(self.template):
            action, slot = self.template[self.step]
            available = [m for m in MAPS if m not in self.picks]
            self.add_item(VetoMapSelect(step_label(action, slot, self.first_team, self.second_team), available))
            self.add_item(SkipVetoButton())
        else:
            self.add_item(EnterLinksButton())
            self.add_item(RedoVetoButton())


class MatchView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.region = None
        self.season = None
        self.skeleton = None
        self.upcoming_by_id = {}
        self.rebuild()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your session.", ephemeral=True)
            return False
        return True

    def prompt(self):
        if self.region is None:
            return "Step 1 of 3. Choose a region:"
        if self.season is None:
            return f"**{self.region.upper()}**\nStep 2 of 3. Choose a season:"
        return f"**{self.region.upper()} / {self.season}**\nStep 3 of 3. Choose the game to fill in:"

    def rebuild(self):
        self.clear_items()
        if self.region is None:
            self.add_item(RegionSelect(list_regions()))
        elif self.season is None:
            self.add_item(SeasonSelect(list_seasons(self.region)))
        else:
            upcoming = [m for m in load_matches(self.region, self.season) if not m.get("completed")]
            self.upcoming_by_id = {m["id"]: m for m in upcoming}
            if upcoming:
                self.add_item(MatchSelect(upcoming))


async def start(interaction):
    if not list_regions():
        await interaction.response.send_message("No regions found under events/.", ephemeral=True)
        return
    view = MatchView(interaction.user.id)
    await interaction.response.send_message(view.prompt(), view=view, ephemeral=True)