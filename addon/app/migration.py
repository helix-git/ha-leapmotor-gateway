"""One time migration of the stored files to the English names of 0.13.

Runs at every start and does nothing once done. For each old file that has no new
counterpart yet: write the new file (atomic), then rename the old one to
`<name>.pre-0.13`. If a start is interrupted in between, the new file exists and the old
one is simply left as it is. Nothing is deleted. Unknown keys are kept unchanged, so no
value can get lost.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re

log = logging.getLogger("leapmotor_gateway")

SUFFIX = ".pre-0.13"

COMMANDS = {
    "verriegeln": "lock", "entriegeln": "unlock", "klima_an": "climate_on", "klima_aus": "climate_off",
    "schnell_heizen": "quick_heat", "schnell_kuehlen": "quick_cool", "scheibe_auftauen": "defrost_windshield",
    "batterie_vorwaermen": "battery_preheat", "batterie_vorwaermen_aus": "battery_preheat_off",
    "fenster_schliessen": "close_windows", "fenster_oeffnen": "open_windows",
    "sonnenrollo_schliessen": "close_sunshade", "sonnenrollo_oeffnen": "open_sunshade",
    "kofferraum_schliessen": "close_trunk", "kofferraum_oeffnen": "open_trunk",
    "laden_starten": "start_charging", "laden_stoppen": "stop_charging",
    "ladekabel_freigeben": "release_charging_cable", "fahrzeug_orten": "locate_vehicle", "aktualisieren": "refresh",
}
# Log entries that are not vehicle commands.
EVENTS = {
    "einstellungen": "settings", "konto_anlegen": "account_add", "konto_passwort": "account_password",
    "konto_loeschen": "account_remove", "konto_umzug": "account_migration", "fahrzeug_neu": "vehicle_new",
    "fahrzeug_aendern": "vehicle_update", "zertifikat": "certificate", "anmeldung": "login",
}
LEVELS = {"frei": "free", "rueckfrage": "approval", "gesperrt": "blocked"}
ROLES = {"voll": "full", "eingeschraenkt": "limited", "lesen": "read"}

SETTINGS = {"notify_ziele": "notify_targets", "freigabe_s": "approval_timeout_s", "stufen": "levels",
            "intervall_aktiv_s": "poll_active_s", "intervall_ruhe_min": "poll_idle_min",
            "fahrtende_min": "trip_end_min", "rueckfrage_push": "approval_push", "rollen": "roles"}
SETTINGS_DROPPED = {"freigabe_benutzer"}

ACCOUNT = {"benutzer": "username", "passwort": "password", "geraet_id": "device_id"}
VEHICLE = {"konto": "account", "typ": "model", "aktiv": "enabled", "kennzeichen": "plate"}
VEHICLE_DROPPED = {"kennzeichen_im_bild"}

TRIP = {"fahrt": "trip", "fahrten": "trips", "ladung": "charge", "ladungen": "charges",
        "seit_laden": "since_charge", "zuletzt": "last", "ende": "end", "verbrauch": "consumption",
        "nachgetragen": "backfilled", "verpasst": "missed", "pause_energie": "pause_energy",
        "start_energie": "start_energy", "beginn": "start", "seit": "since", "geladen": "charged_kwh",
        "soc_von": "soc_from", "soc_bis": "soc_to", "art": "type", "zeit": "time", "energie": "energy",
        "bereit": "ready", "laedt": "charging"}

# Old results that meant success (the integration decides by `ok`).
OK_RESULTS = ("gesendet", "trockenlauf – nicht gesendet", "wartet auf Freigabe")

LOG = {"zeit": "time", "aktion": "action", "quelle": "source", "stufe": "level", "ergebnis": "result",
       "benutzer": "user", "benutzer_id": "user_id", "freigabe": "approval", "antwort": "response"}


def _rename_keys(d: dict, table: dict, dropped: set = frozenset()) -> dict:
    return {table.get(k, k): v for k, v in d.items() if k not in dropped}


def settings(old: dict) -> dict:
    new = _rename_keys(old, SETTINGS, SETTINGS_DROPPED)
    if isinstance(new.get("levels"), dict):
        new["levels"] = {COMMANDS[n]: LEVELS.get(lv, lv) for n, lv in new["levels"].items() if n in COMMANDS}
    if isinstance(new.get("roles"), dict):
        new["roles"] = {uid: ROLES.get(r, r) for uid, r in new["roles"].items()}
    return new


def accounts(old: dict) -> dict:
    return {"accounts": {aid: _rename_keys(a, ACCOUNT) for aid, a in (old.get("konten") or {}).items()},
            "vehicles": {vin: _rename_keys(v, VEHICLE, VEHICLE_DROPPED) for vin, v in (old.get("fahrzeuge") or {}).items()}}


def _trip_value(value):
    if isinstance(value, dict):
        return {TRIP.get(k, k): _trip_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_trip_value(v) for v in value]
    return value


def trip_computer(old: dict) -> dict:
    new = _trip_value(old)
    new["version"] = 2
    return new


def log_entry(old: dict) -> dict:
    new = _rename_keys(old, LOG)
    action = new.get("action")
    if isinstance(action, str):
        new["action"] = COMMANDS.get(action, EVENTS.get(action, action))
    if isinstance(new.get("level"), str):
        new["level"] = LEVELS.get(new["level"], new["level"])
    if "ok" not in new and isinstance(new.get("result"), str):
        new["ok"] = new["result"] in OK_RESULTS
    return new


def _write(path: str, text: str, private: bool):
    tmp = path + ".tmp"
    mode = 0o600 if private else 0o644
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _json_file(folder: str, old_name: str, new_name: str, convert, private: bool = False) -> bool:
    old, new = os.path.join(folder, old_name), os.path.join(folder, new_name)
    if not os.path.exists(old) or os.path.exists(new):
        return False
    with open(old, encoding="utf-8") as fh:
        data = json.load(fh)
    _write(new, json.dumps(convert(data), ensure_ascii=False, indent=1), private)
    os.replace(old, old + SUFFIX)
    return True


def _jsonl_file(folder: str, old_name: str, new_name: str) -> bool:
    old, new = os.path.join(folder, old_name), os.path.join(folder, new_name)
    if not os.path.exists(old) or os.path.exists(new):
        return False
    lines = []
    with open(old, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.dumps(log_entry(json.loads(line)), ensure_ascii=False))
            except (ValueError, AttributeError):
                lines.append(line)               # keep what cannot be read
    _write(new, "".join(f"{x}\n" for x in lines), private=False)
    os.replace(old, old + SUFFIX)
    return True


def _rename(folder: str, old_name: str, new_name: str) -> bool:
    old, new = os.path.join(folder, old_name), os.path.join(folder, new_name)
    if not os.path.exists(old) or os.path.exists(new):
        return False
    os.replace(old, new)
    return True


def run(folder: str) -> list[str]:
    """Migrate everything that is still in the old format. Returns the new file names."""
    if not os.path.isdir(folder):
        return []
    done = []
    steps = [("einstellungen.json", "settings.json", lambda f, o, n: _json_file(f, o, n, settings)),
             ("konten.json", "accounts.json", lambda f, o, n: _json_file(f, o, n, accounts, private=True)),
             ("befehle.jsonl", "commands.jsonl", _jsonl_file),
             ("befehle.jsonl.1", "commands.jsonl.1", _jsonl_file)]
    for path in sorted(glob.glob(os.path.join(folder, "bordcomputer_*.json"))):
        vin = re.sub(r"^bordcomputer_|\.json$", "", os.path.basename(path))
        steps.append((os.path.basename(path), f"trip_computer_{vin}.json",
                      lambda f, o, n: _json_file(f, o, n, trip_computer)))
    for path in sorted(glob.glob(os.path.join(folder, "bildpaket_*.zip"))):
        vin = re.sub(r"^bildpaket_|\.zip$", "", os.path.basename(path))
        steps.append((os.path.basename(path), f"image_pack_{vin}.zip", _rename))
    for path in sorted(glob.glob(os.path.join(folder, "bild_*.png"))):
        vin = re.sub(r"^bild_|\.png$", "", os.path.basename(path))
        steps.append((os.path.basename(path), f"vehicle_{vin}.png", _rename))
    for old_name, new_name, step in steps:
        try:
            if step(folder, old_name, new_name):
                done.append(new_name)
        except Exception as error:               # one broken file must not stop the others or the start
            log.error("Migration of %s failed: %s", old_name, error)
    if done:
        log.info("Migrated to the 0.13 format: %s", ", ".join(done))
    return done
