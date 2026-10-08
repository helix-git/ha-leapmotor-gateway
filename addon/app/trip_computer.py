"""Trip computer: trips and charges from the polls, stored per vehicle.

The gateway sees every poll (every minute while driving and charging) and keeps working
while Home Assistant restarts. It does not calculate costs.

Trip: starts when the car becomes ready. When it switches off, a pause begins. If the car
is ready again within "trip end after" + 5 min, the same trip continues and the pause does
not count as driving time. Otherwise the trip ends with the values at the moment the car
was parked, not later ones: plugging in right away would otherwise charge into the trip.
Less than 3 min of driving time is discarded.
Charge: from "charging" on until "charging" off, plus distance and consumption since the
previous charge.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

MAX_ENTRIES = 200            # per list, newest first
FOR_HA = 20                  # this many go to the integration (attributes stay small)
MIN_DRIVE_H = 0.05           # 3 min


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def empty() -> dict:
    return {"version": 2, "trip": None, "trips": [], "charge": None, "since_charge": None, "charges": [],
            "last": {}}


def _consumption(kwh: Optional[float], km: Optional[float]) -> Optional[float]:
    return round(kwh / km * 100, 1) if kwh is not None and km is not None and km >= 1 else None


def _save_trip(b: dict, t: dict, end: datetime, km: float, energy: float, backfilled: bool = False):
    h = ((end - _t(t["start"])).total_seconds() - t.get("pause_s", 0)) / 3600
    if h < MIN_DRIVE_H:
        return
    distance, kwh = km - t["start_km"], t["start_energy"] - energy
    e = {"start": t["start"], "end": _iso(end), "km": round(distance, 1), "kwh": round(kwh, 2), "h": round(h, 2),
         "consumption": _consumption(kwh, distance)}
    if backfilled:
        e["backfilled"] = True
    b["trips"] = ([e] + b.get("trips", []))[:MAX_ENTRIES]


def process(b: dict, v: dict, now: datetime, trip_end_min: int) -> bool:
    """Apply one poll. True if something changed (then save)."""
    km, energy, soc = v.get("odometer"), v.get("energy"), v.get("battery")
    ready, charging = v.get("ready"), v.get("charging")
    if km is None or energy is None or ready is None or charging is None:
        return False                                     # incomplete response: decide nothing
    last, changed = b.setdefault("last", {}), False

    # Trip
    t = b.get("trip")
    if ready and not last.get("ready"):
        if t and t.get("pause") and (now - _t(t["pause"])).total_seconds() < (trip_end_min + 5) * 60:
            t["pause_s"] = t.get("pause_s", 0) + (now - _t(t["pause"])).total_seconds()   # short stop: continue
            t["pause"] = t["pause_km"] = t["pause_energy"] = None
        else:
            if t and t.get("pause"):                     # old trip still open: end it with the values when parked
                _save_trip(b, t, _t(t["pause"]), t["pause_km"], t["pause_energy"], backfilled=True)
            b["trip"] = {"start": _iso(now), "start_km": km, "start_energy": energy, "pause": None,
                         "pause_km": None, "pause_energy": None, "pause_s": 0}
        changed = True
    elif not ready and last.get("ready") and t and not t.get("pause"):
        t["pause"], t["pause_km"], t["pause_energy"] = _iso(now), km, energy
        changed = True
    t = b.get("trip")
    if t and t.get("pause") and not ready and (now - _t(t["pause"])).total_seconds() >= trip_end_min * 60:
        _save_trip(b, t, _t(t["pause"]), t["pause_km"] if t.get("pause_km") is not None else km,
                   t["pause_energy"] if t.get("pause_energy") is not None else energy)
        b["trip"] = None
        changed = True

    # Missed trip
    # While parked the gateway polls only every 15 min. A short move in between shows only in
    # the odometer. It becomes a backfilled trip with distance and energy but without driving
    # time, because nobody knows it.
    # Shortly after a detected trip the cloud sometimes reports the final odometer late. That is
    # not a new trip.
    latest = (b.get("trips") or [{}])[0].get("end")
    late_update = latest is not None and (now - _t(latest)).total_seconds() < (trip_end_min + 5) * 60
    if not ready and not last.get("ready") and not b.get("trip") and last.get("km") is not None \
            and km - last["km"] >= 0.5 and last.get("time") and not late_update:
        kwh = last["energy"] - energy if last.get("energy") is not None and not charging and not last.get("charging") else None
        e = {"start": last["time"], "end": _iso(now), "km": round(km - last["km"], 1),
             "kwh": round(kwh, 2) if kwh is not None and kwh >= 0 else None, "h": None,
             "consumption": _consumption(kwh, km - last["km"]) if kwh is not None and kwh >= 0 else None,
             "backfilled": True, "missed": True}
        b["trips"] = ([e] + b.get("trips", []))[:MAX_ENTRIES]
        changed = True

    # Charge
    c = b.get("charge")
    if charging and not last.get("charging"):
        b["charge"] = {"start": _iso(now), "km": km, "energy": energy, "soc": soc, "lat": v.get("latitude"),
                       "lon": v.get("longitude"), "dc": bool(v.get("dc_cable"))}
        changed = True
    elif charging and c and v.get("dc_cable") and not c.get("dc"):
        c["dc"] = changed = True
    elif not charging and last.get("charging") and c:
        s = b.get("since_charge") or {}
        distance = c["km"] - s["km"] if s.get("km") is not None else None
        used = s["energy"] - c["energy"] if s.get("energy") is not None else None
        e = {"start": c["start"], "end": _iso(now), "since": s.get("time"),
             "km": round(distance, 1) if distance is not None else None,
             "kwh": round(used, 2) if used is not None else None,
             "consumption": _consumption(used, distance), "charged_kwh": round(energy - c["energy"], 2),
             "soc_from": c.get("soc"), "soc_to": soc, "type": "DC" if c.get("dc") or v.get("dc_cable") else "AC",
             "lat": c.get("lat"), "lon": c.get("lon")}
        b["charges"] = ([e] + b.get("charges", []))[:MAX_ENTRIES]
        b["since_charge"] = {"time": _iso(now), "km": km, "energy": energy}
        b["charge"] = None
        changed = True

    if last.get("ready") != bool(ready) or last.get("charging") != bool(charging):
        last["ready"], last["charging"] = bool(ready), bool(charging)
        changed = True
    if last.get("km") != km:                             # values of the last poll, for missed trips
        changed = True
    last.update(km=km, energy=energy, time=_iso(now))
    return changed


def summary(b: dict, v: dict, now: datetime) -> dict:
    """What the integration shows: current or last trip, since the last charge, the lists."""
    km, energy = v.get("odometer"), v.get("energy")
    t, trip = b.get("trip"), None
    if t and km is not None and energy is not None:
        end = _t(t["pause"]) if t.get("pause") else now
        distance, kwh = km - t["start_km"], t["start_energy"] - energy
        h = max(0.0, ((end - _t(t["start"])).total_seconds() - t.get("pause_s", 0)) / 3600)
        trip = {"ongoing": True, "start": t["start"], "end": None, "km": round(distance, 1), "kwh": round(kwh, 2),
                "h": round(h, 2), "consumption": _consumption(kwh, distance)}
    elif b.get("trips"):
        trip = {**b["trips"][0], "ongoing": False}
    s, since = b.get("since_charge"), None
    if s and km is not None and energy is not None:
        distance, kwh = km - s["km"], s["energy"] - energy
        since = {"time": s["time"], "km": round(distance, 1), "kwh": round(kwh, 2), "consumption": _consumption(kwh, distance)}
    return {"trip": trip, "since_charge": since, "charging_since": (b.get("charge") or {}).get("start"),
            "trips": b.get("trips", [])[:FOR_HA], "charges": b.get("charges", [])[:FOR_HA],
            "trip_count": len(b.get("trips", [])), "charge_count": len(b.get("charges", []))}


def file_name(vin: str) -> str:
    return f"trip_computer_{vin}.json"


class Store:
    """trip_computer_<VIN>.json in the data folder of the gateway."""

    def __init__(self, folder: str, vin: str):
        self.path = os.path.join(folder, file_name(vin))

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return {**empty(), **json.load(fh)}
        except FileNotFoundError:
            return empty()

    def save(self, b: dict):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(b, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
