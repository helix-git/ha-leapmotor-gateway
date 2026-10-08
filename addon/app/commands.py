"""Allowlist of vehicle commands.

Only what is listed here can be reached through the gateway. The library offers
much more: autonomous parking (autopark, piloted_parking), speed limit, software
updates (fota_*), sentry, video, seats, hotspot. None of it is listed, and no
setting can enable it. That would take a change to this code.

Every command has a level:
  free      sent immediately
  approval  sent only after approval in the push notification, on an unlocked phone
  blocked   rejected and logged
The default level is defined here. The app (Security tab) can change it per
command, but never for commands outside this list.

T03 specifics from kerniger/leapmotor-ha 0.7.3 (MIT), model_helpers.py and
api.py: climate off needs a complete payload on the T03, and the climate payload
follows _build_climate_payload. leapmotor-api 0.3.2 sends no payload for
climate off.
"""
import json
from dataclasses import dataclass
from typing import Any, Callable

FREE, APPROVAL, BLOCKED = "free", "approval", "blocked"
LEVELS = (FREE, APPROVAL, BLOCKED)

DEFAULT_TEMPERATURE = 22
T03_AC_OFF_PAYLOAD = {"circle": "out", "mode": "wind", "operate": "off", "position": "all",
                      "temperature": str(DEFAULT_TEMPERATURE), "windlevel": "3", "wshld": "0"}


def climate_payload(params: dict) -> dict:
    """Like _build_climate_payload of the integration: mode cold/hot/wind, 18 to 32 °C,
    fan 1 to 7, recirculation, windshield. 'wind' is sent as 'nohotcold'."""
    mode = params.get("mode") or "nohotcold"
    if mode == "wind":
        mode = "nohotcold"
    temperature = int(params.get("temperature", DEFAULT_TEMPERATURE))
    fan_speed = int(params.get("fan_speed", 4))
    recirculation = bool(params.get("recirculation", False))
    windshield = bool(params.get("windshield_defrost", False))
    if mode not in ("cold", "hot", "nohotcold"):
        raise ValueError("mode must be cold, hot or wind")
    if not 18 <= temperature <= 32:
        raise ValueError("temperature must be between 18 and 32 °C")
    if not 1 <= fan_speed <= 7:
        raise ValueError("fan speed must be between 1 and 7")
    return {"circle": "in" if recirculation else "out", "mode": mode, "operate": "manual", "position": "all",
            "temperature": str(temperature), "windlevel": str(fan_speed), "wshld": "2" if windshield else "1"}


def t03_off_payload(params: dict) -> dict:
    """T03: climate off needs the full payload, and that contains a temperature. It is the
    preset temperature (18 to 32, otherwise 22 °C). If the T03 takes it as its target, the
    temperature can be set without switching climate on. Whether it does is unverified."""
    payload = dict(T03_AC_OFF_PAYLOAD)
    t = params.get("temperature")
    if t not in (None, ""):
        t = int(float(t))
        if not 18 <= t <= 32:
            raise ValueError("temperature must be between 18 and 32 °C")
        payload["temperature"] = str(t)
    return payload


def _climate_off(client, vin: str, params: dict, model: str):
    if model.upper() == "T03":
        # Private in the library, but the only way to send the T03 payload with climate off.
        # The version is pinned in requirements.txt.
        from leapmotor_api.const import REMOTE_CTL_AC_OFF
        return client._remote_control(vin=vin, action=REMOTE_CTL_AC_OFF,
                                      cmd_content=json.dumps(t03_off_payload(params), separators=(",", ":")))
    return client.ac_off(vin)


def _window_value(params: dict, default: int) -> str:
    value = int(params.get("percent", default))
    if not 0 <= value <= 100:
        raise ValueError("window position must be 0 to 100 %")
    return str(value)       # T03: scale 0 to 100 (WINDOW_POSITION_SCALE of the integration)


@dataclass(frozen=True)
class Command:
    name: str
    title: str
    level: str
    run: Callable[[Any, str, dict, str], Any]
    icon: str = "mdi:car"
    button: bool = True          # create a button entity in HA


COMMANDS: dict[str, Command] = {c.name: c for c in [
    # free: closes, heats, cools
    Command("lock", "Lock", FREE, lambda c, v, p, t: c.lock_vehicle(v), "mdi:car-key", button=False),
    Command("climate_on", "Climate on", FREE, lambda c, v, p, t: c.ac_on(v, params=climate_payload(p)), "mdi:air-conditioner", button=False),
    Command("climate_off", "Climate off", FREE, _climate_off, "mdi:fan-off"),
    Command("quick_heat", "Quick heat", FREE, lambda c, v, p, t: c.quick_heat(v), "mdi:heat-wave"),
    Command("quick_cool", "Quick cool", FREE, lambda c, v, p, t: c.quick_cool(v), "mdi:snowflake"),
    Command("defrost_windshield", "Defrost windshield", FREE, lambda c, v, p, t: c.windshield_defrost(v), "mdi:car-defrost-front"),
    Command("battery_preheat", "Preheat battery", FREE, lambda c, v, p, t: c.battery_preheat(v), "mdi:battery-heart-variant"),
    Command("battery_preheat_off", "Battery preheat off", FREE, lambda c, v, p, t: c.battery_preheat_off(v), "mdi:battery-heart-outline"),
    Command("close_windows", "Close windows", FREE, lambda c, v, p, t: c.close_windows(v), "mdi:car-door"),
    Command("close_sunshade", "Close sunshade", FREE, lambda c, v, p, t: c.close_sunshade(v), "mdi:blinds"),
    Command("close_trunk", "Close trunk", FREE, lambda c, v, p, t: c.close_trunk(v), "mdi:car-back"),
    Command("stop_charging", "Stop charging", FREE, lambda c, v, p, t: c.stop_charging(v), "mdi:ev-station"),
    # approval: opens the car or draws attention to it
    Command("unlock", "Unlock", APPROVAL, lambda c, v, p, t: c.unlock_vehicle(v), "mdi:car-key", button=False),
    Command("open_trunk", "Open trunk", APPROVAL, lambda c, v, p, t: c.open_trunk(v), "mdi:car-back"),
    Command("open_windows", "Open windows (gap)", APPROVAL,
            lambda c, v, p, t: c.open_windows(v, value=_window_value(p, 20)), "mdi:car-door"),
    Command("open_sunshade", "Open sunshade", APPROVAL, lambda c, v, p, t: c.open_sunshade(v), "mdi:blinds-open"),
    Command("release_charging_cable", "Release charging cable", APPROVAL, lambda c, v, p, t: c.unlock_charger(v), "mdi:ev-plug-type2"),
    Command("start_charging", "Start charging", APPROVAL, lambda c, v, p, t: c.start_charging(v), "mdi:ev-station"),
    Command("locate_vehicle", "Locate vehicle (horn and lights)", APPROVAL, lambda c, v, p, t: c.find_vehicle(v), "mdi:car-search"),
]}

# Push notifications follow the Home Assistant language. Only the texts a user reads on the phone.
TITLES_DE = {
    "lock": "Verriegeln", "climate_on": "Klima an", "climate_off": "Klima aus", "quick_heat": "Schnell heizen",
    "quick_cool": "Schnell kühlen", "defrost_windshield": "Scheibe auftauen", "battery_preheat": "Batterie vorwärmen",
    "battery_preheat_off": "Batterievorwärmung aus", "close_windows": "Fenster schließen",
    "close_sunshade": "Sonnenrollo schließen", "close_trunk": "Kofferraum schließen", "stop_charging": "Laden stoppen",
    "unlock": "Entriegeln", "open_trunk": "Kofferraum öffnen", "open_windows": "Fenster öffnen (Spalt)",
    "open_sunshade": "Sonnenrollo öffnen", "release_charging_cable": "Ladekabel freigeben",
    "start_charging": "Laden starten", "locate_vehicle": "Fahrzeug orten (Hupe und Licht)",
}


def title(name: str, language: str = "en") -> str:
    if language.lower().startswith("de") and name in TITLES_DE:
        return TITLES_DE[name]
    return COMMANDS[name].title if name in COMMANDS else name


def check_levels(levels: dict) -> dict[str, str]:
    """Deviations per command. Unknown commands and levels are an error, not silently ignored:
    a typo must not leave a command open that should be blocked."""
    result = {}
    for name, level in (levels or {}).items():
        name, level = str(name).strip(), str(level).strip().lower()
        if name not in COMMANDS:
            raise ValueError(f"unknown command: {name}")
        if level not in LEVELS:
            raise ValueError(f"unknown level for {name}: {level} (allowed: {', '.join(LEVELS)})")
        result[name] = level
    return result


def level(name: str, deviations: dict[str, str]) -> str:
    if name not in COMMANDS:
        return BLOCKED
    return deviations.get(name, COMMANDS[name].level)
