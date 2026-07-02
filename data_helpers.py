import json
import os
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Save into the new Astro site's data tree (pcmt2/src/data)
EVENTS_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "pcmt2", "src", "data"))
# Root of the pcmt2 git repo (two levels up from src/data).
REPO_DIR = os.path.normpath(os.path.join(EVENTS_DIR, "..", ".."))


def git_sync(message):
    """Stage src/data, commit, and push to the pcmt2 repo so the site updates.
    Best-effort: returns "" on success / nothing-to-commit, or a short note on
    failure (so callers can surface it without crashing the command)."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never block on a credential prompt

    def run(*args, check=True):
        return subprocess.run(["git", "-C", REPO_DIR, *args],
                              check=check, capture_output=True, text=True, env=env)

    try:
        run("add", "src/data")
        # Nothing staged -> nothing to do.
        if run("diff", "--cached", "--quiet", check=False).returncode == 0:
            return ""
        run("commit", "-m", message)
        # Reconcile with commits pushed from elsewhere before pushing. Abort the
        # rebase on conflict so the repo is never left in a half-rebased state.
        try:
            run("pull", "--rebase")
        except subprocess.CalledProcessError:
            run("rebase", "--abort", check=False)
            raise
        run("push")
        return ""
    except subprocess.CalledProcessError as e:
        out = (e.stderr or e.stdout or "").strip()
        print(f"git sync failed: {out or e}")
        last = out.splitlines()[-1] if out else str(e)
        return f"\n(git sync failed: {last})"

FORMATS = {"BO1": 1, "BO3": 3, "BO5": 5}


# Folder layout is season-first: events/<season>/<region>/
def event_dir(region, season):
    return os.path.join(EVENTS_DIR, season, region)


def list_regions():
    """All regions found across every season folder."""
    if not os.path.isdir(EVENTS_DIR):
        return []
    regions = set()
    for season in os.listdir(EVENTS_DIR):
        sp = os.path.join(EVENTS_DIR, season)
        if not os.path.isdir(sp):
            continue
        for region in os.listdir(sp):
            # A real event folder has an event.json (skips icons/, etc.)
            if os.path.exists(os.path.join(sp, region, "event.json")):
                regions.add(region)
    return sorted(regions)


def list_seasons(region):
    """Seasons that contain this region."""
    if not os.path.isdir(EVENTS_DIR):
        return []
    seasons = []
    for season in sorted(os.listdir(EVENTS_DIR)):
        sp = os.path.join(EVENTS_DIR, season)
        if os.path.exists(os.path.join(sp, region, "event.json")):
            seasons.append(season)
    return seasons


def load_event(region, season):
    path = os.path.join(event_dir(region, season), "event.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def list_stages(region, season):
    event = load_event(region, season)
    # Accept either the website shape (top-level "stages": ["Group Stage", ...]
    # or a list of {"name": ...} objects) or the older bot shape ("format":
    # {"stages": [{"name": ...}]}).
    stages = event.get("stages")
    if not stages:
        stages = event.get("format", {}).get("stages", [])
    names = []
    for s in stages:
        if isinstance(s, str):
            names.append(s)
        elif isinstance(s, dict) and "name" in s:
            names.append(s["name"])
    return names


def load_teams(region, season):
    path = os.path.join(event_dir(region, season), "teams.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def matches_dir(region, season):
    # Individual per-match files live in matches/individual/; the combined
    # matches.json (written by build.py) sits one level up in matches/.
    return os.path.join(event_dir(region, season), "matches", "individual")


def match_file_path(region, season, match_id):
    return os.path.join(matches_dir(region, season), f"{match_id}.json")


def load_matches(region, season):
    """Read every per-match JSON file in the season's matches/ folder."""
    folder = matches_dir(region, season)
    if not os.path.isdir(folder):
        return []
    matches = []
    for fname in sorted(os.listdir(folder)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(folder, fname)) as f:
            matches.append(json.load(f))
    return matches


def team_name(teams, abbr):
    t = teams.get(abbr)
    if isinstance(t, dict):
        return t.get("name", abbr)
    return abbr


def save_upcoming_match(region, season, team1, team2, stage, best_of, date_iso, teams):
    folder = matches_dir(region, season)
    os.makedirs(folder, exist_ok=True)

    stage_slug = stage.lower().replace(" ", "-")
    season_slug = season.lower().replace(" ", "-")
    base_id = f"{team1.lower()}-vs-{team2.lower()}-{season_slug}-{stage_slug}-{date_iso[:10]}"

    # Don't clobber an existing match file (e.g. an already-played match with the
    # same matchup on the same day). Bump a suffix until the filename is free.
    final_id, n = base_id, 2
    while os.path.exists(match_file_path(region, season, final_id)):
        final_id = f"{base_id}-{n}"
        n += 1

    match = {
        "id": final_id,
        "team1": team1,
        "team2": team2,
        "team1Name": team_name(teams, team1),
        "team2Name": team_name(teams, team2),
        "score1": 0,
        "score2": 0,
        "completed": False,
        "bestOf": best_of,
        "date": date_iso,
        "stage": stage,
    }
    with open(match_file_path(region, season, final_id), "w") as f:
        json.dump(match, f, indent=2)
    return match