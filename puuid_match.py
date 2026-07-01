#!/usr/bin/env python3
"""
Dump every player's puuid from a tracker match link.

Use this when the account lookup 404s, for example the player renamed and the
account endpoint can no longer find the old name#tag, or tracker is showing a
masked tag like #0000. A puuid is stable across renames, so pull it from a
match the player actually appeared in.

Usage:
    python puuid_from_match.py <tracker match url>
    python puuid_from_match.py <tracker match url> shipty   # highlight a name
"""
import os
import re
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

MATCH_BASE = "https://api.henrikdev.xyz/valorant/v2/match"
API_KEY = os.getenv("HENRIK_API_KEY")

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def main():
    if not API_KEY:
        print("No HENRIK_API_KEY set. Add it to your .env.")
        sys.exit(1)
    if len(sys.argv) < 2:
        print("Usage: python puuid_from_match.py <tracker match url> [name to highlight]")
        sys.exit(1)

    m = UUID_RE.search(sys.argv[1])
    if not m:
        print("Couldn't find a match id in that URL.")
        sys.exit(1)

    highlight = sys.argv[2].lower() if len(sys.argv) > 2 else None

    resp = requests.get(
        f"{MATCH_BASE}/{m.group(0)}",
        headers={"Authorization": API_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    players = resp.json()["data"]["players"]["all_players"]

    for p in players:
        rid = f"{p['name']}#{p['tag']}"
        mark = "  <--" if highlight and highlight in p["name"].lower() else ""
        print(f"{rid:<28}{p['team']:<6}{p['puuid']}{mark}")


if __name__ == "__main__":
    main()