import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from data_helpers import BASE_DIR

USER_TZ_FILE = os.path.join(BASE_DIR, "user_tz.json")

# Menu label -> IANA zone. zoneinfo applies the correct DST offset automatically
# based on the match date, so each entry covers both codes (EST/EDT) at once.
TZ_ZONES = {
    "EST/EDT": "America/New_York",     # US Eastern
    "CST/CDT": "America/Chicago",      # US Central
    "MST/MDT": "America/Denver",       # US Mountain
    "PST/PDT": "America/Los_Angeles",  # US Pacific
    "GMT/BST": "Europe/London",
    "CET/CEST": "Europe/Paris",
    "AEST": "Australia/Brisbane",      # permanent +10, no DST
}


def load_user_tz_map():
    if os.path.exists(USER_TZ_FILE):
        with open(USER_TZ_FILE) as f:
            return json.load(f)
    return {}


def get_user_tz(user_id):
    tz = load_user_tz_map().get(str(user_id))
    return tz if tz in TZ_ZONES else None


def set_user_tz(user_id, tz):
    m = load_user_tz_map()
    m[str(user_id)] = tz
    with open(USER_TZ_FILE, "w") as f:
        json.dump(m, f, indent=2)


def local_to_utc_iso(d, t, tz_name):
    local_dt = datetime.combine(d, t, tzinfo=ZoneInfo(TZ_ZONES[tz_name]))
    return local_dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")