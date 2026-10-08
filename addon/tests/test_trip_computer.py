"""Trip computer: trips and charges from the polls."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import trip_computer as tc  # noqa: E402

T0 = datetime(2026, 10, 7, 5, 42, tzinfo=timezone.utc)


def v(km, energy, ready=False, charging=False, battery=50, dc=False):
    return {"odometer": km, "energy": energy, "ready": ready, "charging": charging, "battery": battery,
            "dc_cable": dc, "latitude": 52.52, "longitude": 13.405}


def run(b, steps, trip_end=10):
    for minute, values in steps:
        tc.process(b, values, T0 + timedelta(minutes=minute), trip_end)
    return b


def test_trip_with_short_stop():
    """A 6 min stop, then on: one trip, the stop does not count."""
    b = run(tc.empty(), [(0, v(239, 14.0, ready=True)), (5, v(240, 13.8, ready=True)), (12, v(241, 13.5)),
                         (18, v(241, 13.5, ready=True)), (35, v(247, 12.6)), (44, v(247, 12.6)), (46, v(247, 12.6))])
    (t,) = b["trips"]
    assert (t["km"], t["kwh"], t["h"], t["consumption"]) == (8, 1.4, 0.48, 17.5) and b["trip"] is None
    assert t["end"] == (T0 + timedelta(minutes=35)).isoformat()


def test_charging_right_after_parking():
    """Parked, plugged in 3 min later: the trip counts the values at parking."""
    b = run(tc.empty(), [(0, v(249, 10.93, ready=True)), (21, v(253, 10.45)), (24, v(253, 10.9, charging=True)),
                         (32, v(253, 11.31, charging=True))])
    (t,) = b["trips"]
    assert (t["km"], t["kwh"], t["consumption"]) == (4, 0.48, 12.0) and b["charge"]["energy"] == 10.9


def test_trip_end_configurable():
    b = run(tc.empty(), [(0, v(100, 30, ready=True)), (20, v(110, 28)), (26, v(110, 28))], trip_end=5)
    assert len(b["trips"]) == 1


def test_long_stop_two_trips():
    b = run(tc.empty(), [(0, v(100, 30, ready=True)), (20, v(110, 28)), (31, v(110, 28)),
                         (60, v(110, 28, ready=True)), (80, v(120, 26)), (95, v(120, 26))])
    assert [t["km"] for t in b["trips"]] == [10, 10] and not any(t.get("backfilled") for t in b["trips"])


def test_gateway_was_down_old_trip_backfilled():
    b = run(tc.empty(), [(0, v(100, 30, ready=True)), (20, v(110, 28))])       # no poll afterwards
    run(b, [(90, v(110, 28, ready=True))])
    (t,) = b["trips"]
    assert t["backfilled"] and t["end"] == (T0 + timedelta(minutes=20)).isoformat() and b["trip"]["start_km"] == 110


def test_too_short_discarded():
    b = run(tc.empty(), [(0, v(100, 30, ready=True)), (2, v(100, 30)), (13, v(100, 30))])
    assert b["trips"] == [] and b["trip"] is None


def test_charge_with_consumption_since_previous():
    b = tc.empty()
    b["since_charge"] = {"time": "2026-10-06T16:53:00+00:00", "km": 190, "energy": 30.0}
    run(b, [(0, v(239, 10.0, charging=True, battery=30)), (60, v(239, 18.0, charging=True, battery=60, dc=True)),
            (120, v(239, 25.0, battery=76))])
    (c,) = b["charges"]
    assert (c["charged_kwh"], c["soc_from"], c["soc_to"], c["type"], c["km"], c["kwh"], c["consumption"]) == \
        (15.0, 30, 76, "DC", 49, 20.0, 40.8)
    assert (c["lat"], c["lon"]) == (52.52, 13.405)
    assert b["since_charge"]["km"] == 239 and b["since_charge"]["energy"] == 25.0 and b["charge"] is None
    assert c["start"] == T0.isoformat() and c["end"] == (T0 + timedelta(minutes=120)).isoformat()


def test_incomplete_response_decides_nothing():
    b = tc.empty()
    assert tc.process(b, {"odometer": None, "energy": 10, "ready": True, "charging": False}, T0, 10) is False
    assert b["trip"] is None and b["last"] == {}


def test_moved_between_two_polls():
    """Charging ended, car moved, READY never seen: the trip is backfilled without driving time."""
    b = run(tc.empty(), [(0, v(253, 28.2)), (15, v(254, 28.1)), (30, v(254, 28.1))])
    (t,) = b["trips"]
    assert (t["km"], t["kwh"], t["h"], t["missed"]) == (1, 0.1, None, True) and t["consumption"] == 10.0
    assert t["start"] == T0.isoformat() and t["end"] == (T0 + timedelta(minutes=15)).isoformat()
    # a properly detected trip is not counted twice, not even after its end
    b = run(tc.empty(), [(0, v(239, 14.0)), (1, v(239, 14.0, ready=True)), (20, v(247, 12.6)), (31, v(247, 12.6)), (46, v(247, 12.6))])
    assert len(b["trips"]) == 1 and not b["trips"][0].get("missed")
    # moved while charging: distance yes, energy unknown
    b = run(tc.empty(), [(0, v(253, 20.0, charging=True)), (15, v(254, 22.0, charging=True))])
    assert b["trips"][0]["kwh"] is None and b["trips"][0]["km"] == 1


def test_summary_and_store(tmp_path):
    b = run(tc.empty(), [(0, v(100, 30, ready=True))])
    b["since_charge"] = {"time": "2026-10-06T16:53:00+00:00", "km": 90, "energy": 32.0}
    s = tc.summary(b, v(105, 29.0, ready=True), T0 + timedelta(minutes=15))
    assert s["trip"]["ongoing"] and (s["trip"]["km"], s["trip"]["h"]) == (5, 0.25)
    assert (s["since_charge"]["km"], s["since_charge"]["kwh"], s["since_charge"]["consumption"]) == (15, 3.0, 20.0)
    assert set(s) == {"trip", "since_charge", "charging_since", "trips", "charges", "trip_count", "charge_count"}
    store = tc.Store(str(tmp_path), "VIN1")
    store.save(b)
    assert (tmp_path / "trip_computer_VIN1.json").exists()
    assert store.load()["trip"]["start_km"] == 100
    assert {k: store.load()["last"][k] for k in ("ready", "charging", "km")} == {"ready": True, "charging": False, "km": 100}


def test_late_odometer_is_no_trip():
    """The cloud sometimes reports the final odometer only after the trip ended: no phantom trip."""
    b = run(tc.empty(), [(0, v(239, 14.0)), (1, v(239, 14.0, ready=True)), (20, v(247, 12.6)), (31, v(247, 12.6)),
                         (33, v(248, 12.6)), (48, v(248, 12.6))])
    assert len(b["trips"]) == 1 and not b["trips"][0].get("missed")
    # moved again later: that one counts
    b = run(b, [(70, v(249, 12.5))])
    assert len(b["trips"]) == 2 and b["trips"][0]["missed"]
