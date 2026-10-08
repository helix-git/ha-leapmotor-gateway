"""Demo mode: a simulated Leapmotor T03 in Berlin, for trying the app without an account.

No request leaves the app. Commands change the simulated car, so locking, climate and the
approval flow can be tried end to end. The trip computer starts with a few days of made-up
trips and charges around Berlin.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

DEMO_VIN = "LFZ00000000000T03"
DEMO_ACCOUNT = "demo@example.com"
DEMO_NAME = "Leapmotor T03"
DEMO_PLATE = "B-LM 303E"
HOME = (52.5163, 13.3777)          # Pariser Platz, Berlin
BATTERY_KWH = 37.3


class DemoCloud:
    """Stands in for LeapmotorApiClient. Same method names, simulated state."""

    demo = True

    def __init__(self):
        self.locked, self.climate, self.target, self.fan = True, False, 21.0, 3
        self.trunk_open, self.windows, self.sunshade = False, 0, 0
        self.soc, self.odometer, self.outside = 68, 4312, 14
        self.commands: list[tuple] = []

    # Session
    def login(self):
        pass

    def get_vehicle_list(self):
        return [SimpleNamespace(vin=DEMO_VIN, car_type="T03", vehicle_nickname=DEMO_NAME, plate_number=DEMO_PLATE)]

    # Status
    def get_vehicle_status(self, lib_vehicle):
        from leapmotor_api import models as m
        inside = self.target if self.climate else self.outside + 3
        return m.VehicleStatus(
            battery=m.BatteryStatus(soc=self.soc, precise_soc=self.soc + 0.4, charge_state=m.ChargeState.NOT_CHARGING,
                                    charge_remain_time=0, ac_input_slow_charge=0, dc_input_fast_charge=0,
                                    dump_energy=int(BATTERY_KWH * self.soc * 10), battery_current=0.0,
                                    battery_voltage=371.0, expected_mileage=round(265 * self.soc / 100),
                                    min_battery_temp=17),
            driving=m.DrivingStatus(speed=0, total_mileage=self.odometer, gear_status=0,
                                    live_remaining_range=round(250 * self.soc / 100)),
            location=m.LocationStatus(latitude=HOME[0], longitude=HOME[1]),
            climate=m.ClimateStatus(ac_switch=self.climate, ac_setting=self.target, ac_setting_right=self.target,
                                    interior_temp=inside, ac_air_volume=self.fan if self.climate else 0,
                                    ac_cooling_and_heating=2 if self.climate else 0, outdoor_temp=self.outside),
            doors=m.DoorStatus(driver_door_lock_status=self.locked, bbcm_back_door_status=self.trunk_open,
                               lbcm_driver_door_status=False, rbcm_driver_door_status=False,
                               lbcm_left_rear_door_status=False, rbcm_right_rear_door_status=False),
            windows=m.WindowStatus(left_front_window_percent=self.windows, right_front_window_percent=self.windows,
                                   left_rear_window_percent=0, right_rear_window_percent=0, sun_shade=self.sunshade),
            tires=m.TirePressure(front_left_kpa=271, front_right_kpa=269, rear_left_kpa=268, rear_right_kpa=270),
            connectivity=m.ConnectivityStatus(), seat_comfort=m.SeatComfortStatus(), security=m.SecurityStatus(),
            ignition=m.IgnitionStatus(bcm_key_position_on3=False),
            collect_time=int(time.time() * 1000), create_time=None, raw={})

    # Extras
    def get_consumption_last_week_breakdown(self, lib_vehicle):
        return SimpleNamespace(driver_ec=31.4, ac_ec=3.9, other_ec=1.6)

    def today_breakdown(self, lib_vehicle, time_zone: str = "UTC"):
        return SimpleNamespace(driver_ec=4.2, ac_ec=0.6, other_ec=0.2)

    def distance_energy_7_days(self, lib_vehicle) -> dict:
        return {"data": {"totalEnergy": 36.9, "totalAccumulatedMileage": 236}}

    def get_mileage_energy_detail(self, lib_vehicle) -> dict:
        return self.distance_energy_7_days(lib_vehicle)

    def get_consumption_weekly_rank(self, lib_vehicle):
        return SimpleNamespace(weekly=[SimpleNamespace(hundred_km_ec=v) for v in (14.8, 15.6, 15.1, 14.2, 16.0, 15.3)])

    def get_message_list(self, page_no=1, page_size=10):
        return SimpleNamespace(messages=[])

    def get_car_picture(self, lib_vehicle):
        raise RuntimeError("no vehicle image in demo mode")

    # Commands
    def _done(self, name, **change):
        self.commands.append((name, change))
        for k, v in change.items():
            setattr(self, k, v)
        return {"code": 0}

    def lock_vehicle(self, vin):
        return self._done("lock", locked=True)

    def unlock_vehicle(self, vin):
        return self._done("unlock", locked=False)

    def ac_on(self, vin, params=None):
        p = params or {}
        return self._done("climate_on", climate=True, target=float(p.get("temperature", p.get("temp", self.target))))

    def ac_off(self, vin, params=None):
        return self._done("climate_off", climate=False)

    def open_trunk(self, vin):
        return self._done("open_trunk", trunk_open=True)

    def close_trunk(self, vin):
        return self._done("close_trunk", trunk_open=False)

    def open_windows(self, vin, *a, **kw):
        return self._done("open_windows", windows=10)

    def close_windows(self, vin):
        return self._done("close_windows", windows=0)

    def open_sunshade(self, vin):
        return self._done("open_sunshade", sunshade=100)

    def close_sunshade(self, vin):
        return self._done("close_sunshade", sunshade=0)

    def __getattr__(self, name):
        # Remaining commands (quick heat, preheat, charging, locate) are accepted and change nothing.
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **kw: self._done(name)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def seed_trip_computer(folder: str, odometer: int = 4312, soc: int = 68, now: datetime | None = None):
    """A few days of made-up trips and charges, written once. Existing data is never touched."""
    path = os.path.join(folder, f"trip_computer_{DEMO_VIN}.json")
    if os.path.exists(path):
        return
    now = now or datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # (days ago, start hour, minutes, km, kWh)
    plan = [(0, 8.2, 26, 11.8, 1.9), (0, 12.5, 18, 6.4, 1.1), (1, 7.9, 34, 17.3, 2.6), (1, 17.6, 41, 18.1, 3.0),
            (2, 10.1, 52, 31.6, 4.9), (2, 15.4, 47, 29.8, 4.4), (3, 9.0, 22, 8.7, 1.5), (4, 18.2, 15, 4.9, 0.9)]
    trips = []
    for ago, hour, minutes, km, kwh in plan:
        start = day - timedelta(days=ago) + timedelta(hours=hour)
        if start + timedelta(minutes=minutes) > now:
            start = now - timedelta(minutes=minutes + 30 * (len(trips) + 1))
        trips.append({"start": _iso(start), "end": _iso(start + timedelta(minutes=minutes)), "km": km, "kwh": kwh,
                      "h": round(minutes / 60, 2), "consumption": round(kwh / km * 100, 1)})
    trips.sort(key=lambda t: t["start"], reverse=True)
    charges = [
        {"start": _iso(day - timedelta(days=2, hours=-19)), "end": _iso(day - timedelta(days=2, hours=-21, minutes=-40)),
         "since": _iso(day - timedelta(days=6)), "km": 142.3, "kwh": 21.6, "consumption": 15.2, "charged_kwh": 25.7,
         "soc_from": 22, "soc_to": 91, "type": "AC", "lat": 52.5096, "lon": 13.3762},
        {"start": _iso(day - timedelta(days=6, hours=-13)), "end": _iso(day - timedelta(days=6, hours=-13, minutes=-38)),
         "since": _iso(day - timedelta(days=11)), "km": 188.0, "kwh": 28.4, "consumption": 15.1, "charged_kwh": 24.1,
         "soc_from": 14, "soc_to": 79, "type": "DC", "lat": 52.5390, "lon": 13.4247},
    ]
    # Everything since the last charge has to add up: the trips after it, from 91 % down to today's level.
    after = [t for t in trips if t["start"] > charges[0]["end"]]
    km, kwh = round(sum(t["km"] for t in after), 1), round(sum(t["kwh"] for t in after), 2)
    data = {"version": 2, "trip": None, "trips": trips, "charge": None, "charges": charges,
            "since_charge": {"time": charges[0]["end"], "km": odometer - km, "energy": BATTERY_KWH * soc / 100 + kwh},
            "last": {}}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)
