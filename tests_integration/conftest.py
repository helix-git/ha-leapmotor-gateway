"""Home Assistant test environment (pytest-homeassistant-custom-component). The app is mocked, nothing
reaches a car or the app."""
import copy
from unittest.mock import AsyncMock, patch

import pytest

VIN = "LFZ00000000000047"
VIN2 = "LFZ00000000000099"

VALUES = {"battery": 37, "range": 92, "energy": 14.01, "odometer": 239, "locked": True,
          "driver_door": False, "trunk": False, "charging": False, "dc_cable": False, "ready": False,
          "climate": False, "climate_mode": "cool", "target_temp_left": 32, "fan_speed": 5, "outside_temp": 17,
          "tyre_fl": 2.69, "tyre_fr": 2.66, "tyre_rl": 2.58, "tyre_rr": 2.69,
          "latitude": 52.52, "longitude": 13.405, "vehicle_time": "2026-10-06T18:23:13+00:00",
          "charge_state": "not_charging", "ac_cable": False}

TRIP_COMPUTER = {
    "trip": {"ongoing": False, "start": "2026-10-07T05:42:00+00:00", "end": "2026-10-07T06:17:00+00:00",
             "km": 8.0, "kwh": 1.48, "h": 0.48, "consumption": 18.5},
    "since_charge": {"time": "2026-10-06T16:53:00+00:00", "km": 57.0, "kwh": 9.1, "consumption": 16.0},
    "charging_since": None, "trip_count": 9, "charge_count": 2,
    "trips": [{"start": "2026-10-07T05:42:00+00:00", "end": "2026-10-07T06:17:00+00:00", "km": 8.0, "kwh": 1.48,
               "h": 0.48, "consumption": 18.5}],
    "charges": [{"start": "2026-10-06T16:33:00+00:00", "end": "2026-10-06T16:53:00+00:00", "since": None, "km": None,
                 "kwh": None, "consumption": None, "charged_kwh": 7.9, "soc_from": 76, "soc_to": 99, "type": "DC",
                 "lat": 52.5000, "lon": 13.4000}]}


def vehicle(name, vin, battery=37):
    return {"vin": vin, "name": name, "model": "T03", "enabled": True, "pin_set": False, "status": "ok",
            "last_poll": "2026-10-07T00:00:00+00:00", "values": {**VALUES, "battery": battery},
            "extras": {"week_driving_percent": 92.2, "total_energy_kwh": 39.0, "last_message": "Vehicle shared"},
            "image_hash": "abc", "approvals_pending": 0,
            "last_command": {"action": "climate_off", "result": "dry run, not sent", "vin": vin},
            "trip_end_min": 10, "plate": "B-LM 123E", "trip_computer": copy.deepcopy(TRIP_COMPUTER)}


STATE = {
    "gateway": {"status": "1 vehicle active", "cloud_enabled": True, "dry_run": True, "approvals_pending": 0,
                "approval_push": True, "levels": {"lock": "free", "quick_heat": "free", "unlock": "blocked"},
                "commands": {"lock": {"title": "Lock", "icon": "mdi:car-key", "button": False, "level": "free"},
                             "quick_heat": {"title": "Quick heat", "icon": "mdi:heat-wave", "button": True, "level": "free"},
                             "climate_off": {"title": "Climate off", "icon": "mdi:fan-off", "button": True, "level": "free"},
                             "unlock": {"title": "Unlock", "icon": "mdi:car-key", "button": False, "level": "blocked"},
                             "new_feature": {"title": "Something new", "icon": "mdi:star", "button": True, "level": "free"}}},
    "vehicles": {VIN: vehicle("Leapmotor T03", VIN)},
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def gateway():
    state = copy.deepcopy(STATE)
    with patch("custom_components.leapmotor_gateway.api.GatewayClient.vehicles", AsyncMock(return_value=state)) as v, \
         patch("custom_components.leapmotor_gateway.api.GatewayClient.command",
               AsyncMock(return_value={"result": "dry run, not sent"})) as c, \
         patch("custom_components.leapmotor_gateway.api.GatewayClient.image", AsyncMock(return_value=b"\x89PNG")) as image:
        yield type("G", (), {"vehicles": v, "command": c, "image": image, "data": state})
