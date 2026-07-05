import json
import os
from datetime import datetime, timedelta

from data_helpers import BASE_DIR

USER_TZ_FILE = os.path.join(BASE_DIR, "user_tz.json")

# Plain fixed UTC offsets, UTC-12 .. UTC+12. Users pick their current offset
# directly and handle DST themselves (e.g. UTC+1 for BST, UTC+0 for GMT).
TZ_OFFSETS = {f"UTC{h:+d}": h for h in range(-12, 13)}


def load_user_tz_map():
    if os.path.exists(USER_TZ_FILE):
        with open(USER_TZ_FILE) as f:
            return json.load(f)
    return {}


def get_user_tz(user_id):
    tz = load_user_tz_map().get(str(user_id))
    return tz if tz in TZ_OFFSETS else None


def set_user_tz(user_id, tz):
    m = load_user_tz_map()
    m[str(user_id)] = tz
    with open(USER_TZ_FILE, "w") as f:
        json.dump(m, f, indent=2)


def local_to_utc_iso(d, t, tz_name):
    offset = TZ_OFFSETS[tz_name]
    utc_dt = datetime.combine(d, t) - timedelta(hours=offset)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")