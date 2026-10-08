"""Check licence plates and write them in one format.

The plate is set per vehicle in the app (Accounts, Vehicles). Home Assistant shows it as a
sensor and in the dashboard. The vehicle image stays as Leapmotor delivers it.
"""
from __future__ import annotations

import re
from typing import Optional

GERMAN = re.compile(r"^([A-ZÄÖÜ]{1,3})[ -]+([A-Z]{1,2}) ?(\d{1,4}) ?([EH]?)$")
ALLOWED = re.compile(r"^[A-Z0-9ÄÖÜ][A-Z0-9ÄÖÜ -]{0,11}$")


def normalize(text: Optional[str]) -> str:
    """'Mü AB12 E' becomes 'MÜ-AB 12E'. Empty stays empty. Otherwise ValueError."""
    t = re.sub(r"\s+", " ", (text or "").strip().upper())
    if not t:
        return ""
    if m := GERMAN.match(t):
        return f"{m[1]}-{m[2]} {m[3]}{m[4]}"
    if not ALLOWED.match(t):
        raise ValueError("plate: up to 12 characters from letters, digits, space and hyphen")
    return t
