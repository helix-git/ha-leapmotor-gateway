"""Migration of the stored files from the formats before 0.13 (German keys and file names).
The fixtures are written exactly as 0.12 stored them."""
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import migration  # noqa: E402

VIN = "LFZ00000000000047"

OLD_ACCOUNTS = {
    "konten": {"a1b2c3d4": {"benutzer": "leap@example.org", "passwort": "secret-pw", "geraet_id": "0123456789abcdef"}},
    "fahrzeuge": {VIN: {"konto": "a1b2c3d4", "name": "Leapmotor T03", "typ": "T03", "aktiv": True, "pin": "1234",
                        "kennzeichen": "B-LM 2026E", "kennzeichen_im_bild": False}},
}
OLD_SETTINGS = {
    "notify_ziele": ["notify.mobile_app_phone"], "freigabe_benutzer": ["admin-id"], "freigabe_s": 180,
    "stufen": {"verriegeln": "frei", "entriegeln": "gesperrt", "fahrzeug_orten": "frei", "kofferraum_oeffnen": "rueckfrage",
               "autopark": "frei"},
    "intervall_aktiv_s": 60, "intervall_ruhe_min": 20, "fahrtende_min": 7, "rueckfrage_push": True,
    "rollen": {"admin-id": "voll", "kim-id": "lesen", "sam-id": "eingeschraenkt"},
}
OLD_TRIPS = {
    "version": 1,
    "fahrt": {"start": "2026-10-07T05:42:00+00:00", "start_km": 239, "start_energie": 14.0, "pause": None,
              "pause_km": None, "pause_energie": None, "pause_s": 0},
    "fahrten": [{"start": "2026-10-06T16:00:00+00:00", "ende": "2026-10-06T16:30:00+00:00", "km": 8, "kwh": 1.4, "h": 0.48,
                 "verbrauch": 17.5},
                {"start": "2026-10-06T12:00:00+00:00", "ende": "2026-10-06T12:15:00+00:00", "km": 1, "kwh": 0.1, "h": None,
                 "verbrauch": 10.0, "nachgetragen": True, "verpasst": True}],
    "ladung": None,
    "seit_laden": {"zeit": "2026-10-06T16:53:00+00:00", "km": 231, "energie": 25.0},
    "ladungen": [{"beginn": "2026-10-06T07:53:24+00:00", "ende": "2026-10-06T08:33:32+00:00", "seit": None, "km": 49,
                  "kwh": 20.0, "verbrauch": 40.8, "geladen": 7.16, "soc_von": 30, "soc_bis": 76, "art": "AC",
                  "lat": 52.52, "lon": 13.405}],
    "zuletzt": {"bereit": False, "laedt": False, "km": 239, "energie": 14.0, "zeit": "2026-10-07T05:40:00+00:00"},
}
OLD_LOG = [
    {"zeit": "2026-10-07T05:00:00+00:00", "aktion": "entriegeln", "params": {}, "quelle": "ha", "stufe": "rueckfrage",
     "ergebnis": "wartet auf Freigabe", "benutzer_id": "admin-id", "benutzer": "Alex", "vin": VIN, "freigabe": "abcdef0123"},
    {"zeit": "2026-10-07T05:01:00+00:00", "aktion": "klima_an", "params": {"modus": "hot"}, "quelle": "ha", "stufe": "frei",
     "ergebnis": "gesendet", "antwort": "{'code': 0}", "vin": VIN},
    {"zeit": "2026-10-07T05:02:00+00:00", "aktion": "einstellungen", "params": {}, "quelle": "app", "stufe": "-",
     "ergebnis": "geändert", "benutzer": "alex"},
]


def _write_old(folder):
    with open(os.path.join(folder, "konten.json"), "w", encoding="utf-8") as fh:
        json.dump(OLD_ACCOUNTS, fh)
    os.chmod(os.path.join(folder, "konten.json"), 0o600)
    with open(os.path.join(folder, "einstellungen.json"), "w", encoding="utf-8") as fh:
        json.dump(OLD_SETTINGS, fh)
    with open(os.path.join(folder, f"bordcomputer_{VIN}.json"), "w", encoding="utf-8") as fh:
        json.dump(OLD_TRIPS, fh)
    with open(os.path.join(folder, "befehle.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in OLD_LOG) + "not json\n")
    for name, data in ((f"bildpaket_{VIN}.zip", b"zip"), (f"bild_{VIN}.png", b"png"), ("app_cert.pem", b"cert"),
                       ("app_key.pem", b"key"), ("integration_token", b"token")):
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(data)


def _read(folder, name):
    with open(os.path.join(folder, name), encoding="utf-8") as fh:
        return json.load(fh)


def test_everything_migrated_nothing_lost(tmp_path):
    _write_old(tmp_path)
    done = migration.run(str(tmp_path))
    assert set(done) == {"settings.json", "accounts.json", "commands.jsonl", f"trip_computer_{VIN}.json",
                         f"image_pack_{VIN}.zip", f"vehicle_{VIN}.png"}
    a = _read(tmp_path, "accounts.json")
    assert a["accounts"]["a1b2c3d4"] == {"username": "leap@example.org", "password": "secret-pw", "device_id": "0123456789abcdef"}
    assert a["vehicles"][VIN] == {"account": "a1b2c3d4", "name": "Leapmotor T03", "model": "T03", "enabled": True,
                                  "pin": "1234", "plate": "B-LM 2026E"}
    assert stat.S_IMODE(os.stat(tmp_path / "accounts.json").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "konten.json.pre-0.13").st_mode) == 0o600
    s = _read(tmp_path, "settings.json")
    assert s == {"notify_targets": ["notify.mobile_app_phone"], "approval_timeout_s": 180,
                 "levels": {"lock": "free", "unlock": "blocked", "locate_vehicle": "free", "open_trunk": "approval"},
                 "poll_active_s": 60, "poll_idle_min": 20, "trip_end_min": 7, "approval_push": True,
                 "roles": {"admin-id": "full", "kim-id": "read", "sam-id": "limited"}}
    t = _read(tmp_path, f"trip_computer_{VIN}.json")
    assert t["version"] == 2 and set(t) == {"version", "trip", "trips", "charge", "since_charge", "charges", "last"}
    assert t["trip"]["start_energy"] == 14.0 and t["trips"][0]["end"] == "2026-10-06T16:30:00+00:00"
    assert t["trips"][1] == {"start": "2026-10-06T12:00:00+00:00", "end": "2026-10-06T12:15:00+00:00", "km": 1, "kwh": 0.1,
                             "h": None, "consumption": 10.0, "backfilled": True, "missed": True}
    assert t["charges"][0] == {"start": "2026-10-06T07:53:24+00:00", "end": "2026-10-06T08:33:32+00:00", "since": None,
                               "km": 49, "kwh": 20.0, "consumption": 40.8, "charged_kwh": 7.16, "soc_from": 30,
                               "soc_to": 76, "type": "AC", "lat": 52.52, "lon": 13.405}
    assert t["since_charge"] == {"time": "2026-10-06T16:53:00+00:00", "km": 231, "energy": 25.0}
    assert t["last"] == {"ready": False, "charging": False, "km": 239, "energy": 14.0, "time": "2026-10-07T05:40:00+00:00"}
    lines = (tmp_path / "commands.jsonl").read_text(encoding="utf-8").splitlines()
    first, second, third = (json.loads(x) for x in lines[:3])
    assert first == {"time": "2026-10-07T05:00:00+00:00", "action": "unlock", "params": {}, "source": "ha", "level": "approval",
                     "result": "wartet auf Freigabe", "user_id": "admin-id", "user": "Alex", "vin": VIN,
                     "approval": "abcdef0123", "ok": True}
    assert (second["action"], second["level"], second["response"], second["ok"]) == ("climate_on", "free", "{'code': 0}", True)
    assert (third["action"], third["ok"]) == ("settings", False)
    assert lines[3] == "not json"                                    # what cannot be read is kept as it is
    assert (tmp_path / f"image_pack_{VIN}.zip").read_bytes() == b"zip" and (tmp_path / f"vehicle_{VIN}.png").read_bytes() == b"png"
    for old in ("konten.json", "einstellungen.json", f"bordcomputer_{VIN}.json", "befehle.jsonl"):
        assert not (tmp_path / old).exists() and (tmp_path / (old + ".pre-0.13")).exists()
    for kept in ("app_cert.pem", "app_key.pem", "integration_token"):
        assert (tmp_path / kept).exists()


def test_second_start_changes_nothing(tmp_path):
    _write_old(tmp_path)
    migration.run(str(tmp_path))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert migration.run(str(tmp_path)) == []
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_interrupted_start_keeps_both(tmp_path):
    """New file already written, old one not yet renamed: the new one wins, the old one stays."""
    _write_old(tmp_path)
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": {}, "vehicles": {}}))
    migration.run(str(tmp_path))
    assert (tmp_path / "konten.json").exists() and _read(tmp_path, "accounts.json") == {"accounts": {}, "vehicles": {}}


def test_broken_file_does_not_stop_the_others(tmp_path):
    _write_old(tmp_path)
    (tmp_path / "einstellungen.json").write_text("{broken")
    done = migration.run(str(tmp_path))
    assert "settings.json" not in done and "accounts.json" in done
    assert (tmp_path / "einstellungen.json").read_text() == "{broken"           # left for a look by hand


def test_gateway_starts_on_old_data(tmp_path, monkeypatch):
    """The whole start on a 0.12 folder: accounts, PIN, roles, levels and trips are there."""
    for k in list(os.environ):
        if k.startswith("LG_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("LG_STATE_DIR", str(tmp_path))
    _write_old(tmp_path)
    import gateway as gw
    s = gw.Settings()
    g = gw.Gateway(s, client_factory=lambda a: None, ha=None)
    assert g.store.vehicle(VIN)["pin"] == "1234" and g.store.account("a1b2c3d4")["password"] == "secret-pw"
    assert s.role("admin-id") == "full" and s.approval_timeout_s == 180 and s.trip_end_min == 7
    assert s.ui_values()["levels"]["unlock"] == "blocked"
    d = g.vehicle_data(VIN)
    assert d["plate"] == "B-LM 2026E" and d["trip_computer"]["trip_count"] == 2 and d["trip_computer"]["charge_count"] == 1
    assert g.image_path(VIN) and g.log.recent(5)[-1]["action"] == "unlock"


@pytest.mark.parametrize("old, new", [("verriegeln", "lock"), ("fahrzeug_orten", "locate_vehicle"), ("aktualisieren", "refresh"),
                                      ("zertifikat", "certificate"), ("etwas_neues", "etwas_neues")])
def test_action_names(old, new):
    assert migration.log_entry({"aktion": old})["action"] == new
