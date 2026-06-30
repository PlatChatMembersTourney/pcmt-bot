import json
import os
from datetime import datetime, timedelta

from data_helpers import BASE_DIR

USER_TZ_FILE = os.path.join(BASE_DIR, "user_tz.json")

TZ_OFFSETS = {
    "AEST (UTC+10)": 10,
    "AEDT (UTC+11)": 11,
    "GMT (UTC+0)": 0,
    "BST (UTC+1)": 1,
    "CET (UTC+1)": 1,
    "CEST (UTC+2)": 2,
    "EST (UTC-5)": -5,
    "EDT (UTC-4)": -4,
    "CST (UTC-6)": -6,
    "PST (UTC-8)": -8,
}


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