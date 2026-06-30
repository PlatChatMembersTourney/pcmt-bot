import json
import os
from collections import defaultdict


def generate_agent_stats(event_dir, matches):
    completed = [m for m in matches if m.get("completed")]
    if not completed:
        print("  agent-stats.json: no completed matches, skipping")
        return

    teams_path = os.path.join(event_dir, "teams.json")
    teams = {}
    if os.path.exists(teams_path):
        with open(teams_path) as fp:
            teams = json.load(fp)

    # Region-wide per map (top table)
    map_comps = defaultdict(int)                       # map -> number of team-comps run
    map_agent = defaultdict(lambda: defaultdict(int))  # map -> agent -> comps that included it
    map_rounds = defaultdict(lambda: {"played": 0, "atk": 0, "atk_won": 0, "def": 0, "def_won": 0})

    # Per map per team (bottom grid) -> set of agents
    map_team_agents = defaultdict(lambda: defaultdict(set))

    for match in completed:
        for detail in match.get("mapDetails", []):
            map_name = detail.get("name", "")
            if not map_name:
                continue
            map_rounds[map_name]["played"] += 1

            for block in detail.get("stats", []):
                team = block.get("team", "")
                map_comps[map_name] += 1
                for p in block.get("players", []):
                    agent = p.get("Agent", "")
                    if not agent:
                        continue
                    if agent not in map_team_agents[map_name][team]:
                        map_team_agents[map_name][team].add(agent)
                        map_agent[map_name][agent] += 1

            # atk/def round split, pooled (side belongs to the round winner)
            for rnd in detail.get("rounds", []):
                side = rnd.get("side", "")
                if side == "atk":
                    map_rounds[map_name]["atk"] += 1
                    map_rounds[map_name]["atk_won"] += 1
                    map_rounds[map_name]["def"] += 1
                elif side == "def":
                    map_rounds[map_name]["def"] += 1
                    map_rounds[map_name]["def_won"] += 1
                    map_rounds[map_name]["atk"] += 1

    result = {}
    for map_name in sorted(map_comps.keys()):
        comps = map_comps[map_name]
        r = map_rounds[map_name]
        agents = [{
            "agent": agent,
            "comps": cnt,
            "pickPct": round(cnt / comps, 3) if comps else 0,
        } for agent, cnt in map_agent[map_name].items()]
        agents.sort(key=lambda a: a["comps"], reverse=True)

        team_grid = {}
        for team in sorted(map_team_agents[map_name].keys()):
            team_grid[team] = {
                "team": team,
                "teamName": teams[team]["name"] if team in teams else team,
                "agents": sorted(map_team_agents[map_name][team]),
            }

        result[map_name] = {
            "mapsPlayed": r["played"],
            "atkPct": round(r["atk_won"] / r["atk"], 3) if r["atk"] else 0,
            "defPct": round(r["def_won"] / r["def"], 3) if r["def"] else 0,
            "agents": agents,
            "teams": team_grid,
        }

    out_path = os.path.join(event_dir, "agent-stats.json")
    with open(out_path, "w") as fp:
        json.dump(result, fp, indent=2)

    print(f"  agent-stats.json: {len(result)} maps")