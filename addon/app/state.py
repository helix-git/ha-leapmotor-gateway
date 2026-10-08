"""Vehicle status to flat values.

The values come from leapmotor_api.VehicleStatus. Where the meaning of a field is
uncertain (sign of the battery current, charging power), it is noted here.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


def _g(obj, *path, default=None):
    for part in path:
        if obj is None:
            return default
        obj = getattr(obj, part, None)
    return default if obj is None else obj


def _enum(value) -> Optional[str]:
    return None if value is None else getattr(value, "name", str(value)).lower()


GEARS = {0: "park", 1: "drive", 2: "neutral", 3: "reverse"}
CLIMATE_MODES = {0: "fan", 1: "cool", 2: "heat"}


def _name(value, table: dict) -> Optional[str]:
    """Enum or raw number to name. The T03 reports gear and climate mode as numbers,
    not as library enums."""
    if value is None:
        return None
    number = getattr(value, "value", value)
    try:
        return table.get(int(number), str(number))
    except (TypeError, ValueError):
        return _enum(value)


def _bar(kpa) -> Optional[float]:
    return None if kpa is None else round(kpa / 100, 2)


def to_utc(value) -> Optional[str]:
    """Time of the vehicle report as UTC ISO string.

    leapmotor_api returns collect_time without a zone. Taken from the API field
    "collectTime" it is UTC. When the library derives it from the signal "sts", it is
    local time (fromtimestamp). So a naive value is read as UTC, and if that lies in the
    future it was local time."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            as_utc = value.replace(tzinfo=timezone.utc)
            if as_utc <= datetime.now(timezone.utc) + timedelta(minutes=5):
                return as_utc.isoformat()
        return value.astimezone(timezone.utc).isoformat()
    try:                                   # milliseconds since 1970
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError):
        return str(value)


def values(status) -> dict[str, Any]:
    b, d, k = status.battery, status.driving, status.climate
    doors, win, tyres = status.doors, status.windows, status.tires
    charge = _enum(_g(b, "charge_state"))
    current, voltage = _g(b, "battery_current"), _g(b, "battery_voltage")
    return {
        "battery": _g(b, "soc"),
        "battery_precise": _g(b, "precise_soc"),
        # T03: live_remaining_range stays empty, the range is in expectedMileage.
        "range": _g(d, "live_remaining_range") if _g(d, "live_remaining_range") is not None else _g(b, "expected_mileage"),
        # dumpEnergy in Wh.
        "energy": round(_g(b, "dump_energy") / 1000, 2) if _g(b, "dump_energy") is not None else None,
        "odometer": _g(d, "total_mileage"),
        "speed": _g(d, "speed"),
        "gear": _name(_g(d, "gear_status"), GEARS),
        "charge_state": charge,
        "charging": charge == "charging",
        "ac_cable": bool(_g(b, "ac_input_slow_charge")) if _g(b, "ac_input_slow_charge") is not None else None,
        "dc_cable": bool(_g(b, "dc_input_fast_charge")) if _g(b, "dc_input_fast_charge") is not None else None,
        "charge_time_left": _g(b, "charge_remain_time"),
        "battery_current": current,
        "battery_voltage": voltage,
        # The sign of the current while charging is unknown, so the absolute value. Only meaningful while charging.
        "charging_power": round(abs(current * voltage) / 1000, 2) if charge == "charging" and current is not None and voltage is not None else 0.0,
        "battery_temp_min": _g(b, "min_battery_temp"),
        "locked": _g(doors, "is_locked"),
        "driver_door": _g(doors, "lbcm_driver_door_status"),
        "passenger_door": _g(doors, "rbcm_driver_door_status"),
        "rear_left_door": _g(doors, "lbcm_left_rear_door_status"),
        "rear_right_door": _g(doors, "rbcm_right_rear_door_status"),
        "trunk": _g(doors, "bbcm_back_door_status"),
        "window_fl": _g(win, "left_front_window_percent"),
        "window_fr": _g(win, "right_front_window_percent"),
        "window_rl": _g(win, "left_rear_window_percent"),
        "window_rr": _g(win, "right_rear_window_percent"),
        "sunshade": _g(win, "sun_shade"),
        "climate": _g(k, "ac_switch"),
        "inside_temp": _g(k, "interior_temp"),
        "outside_temp": _g(k, "outdoor_temp"),
        "target_temp_left": _g(k, "ac_setting"),
        "target_temp_right": _g(k, "ac_setting_right"),
        "fan_speed": _g(k, "ac_air_volume"),
        "climate_mode": _name(_g(k, "ac_cooling_and_heating"), CLIMATE_MODES),
        "tyre_fl": _bar(_g(tyres, "front_left_kpa")),
        "tyre_fr": _bar(_g(tyres, "front_right_kpa")),
        "tyre_rl": _bar(_g(tyres, "rear_left_kpa")),
        "tyre_rr": _bar(_g(tyres, "rear_right_kpa")),
        "ready": bool(_g(status, "ignition", "bcm_key_position_on3")) if _g(status, "ignition", "bcm_key_position_on3") is not None else None,
        "latitude": _g(status, "location", "latitude"),
        "longitude": _g(status, "location", "longitude"),
        "vehicle_time": to_utc(_g(status, "collect_time")),
    }


def active(v: dict) -> bool:
    """Fast polling while something happens, like the eco polling of the integration:
    only locked, parked and unplugged counts as idle."""
    return bool(v.get("ready") or v.get("charging") or v.get("ac_cable") or v.get("dc_cable")
                or v.get("locked") is False or (v.get("speed") or 0) > 0 or v.get("climate"))
