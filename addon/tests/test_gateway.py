"""Tests for the Leapmotor Gateway. Cloud and Home Assistant are simulated. These tests never
send anything to a car."""
import io
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import accounts as ac  # noqa: E402
import commands as cmd  # noqa: E402
import gateway as gw  # noqa: E402
import plate as pl  # noqa: E402
import state as st  # noqa: E402

VIN = "LFZ00000000000047"
VIN2 = "LFZ00000000000099"


class FakeCloud:
    """Records every call together with the PIN that was set at that moment."""

    def __init__(self, vehicles=(VIN,)):
        self.calls, self.operation_password, self._vins = [], None, vehicles

    def login(self):
        self.calls.append(("login", (), {}, None))

    def get_vehicle_list(self):
        return [SimpleNamespace(vin=v, car_type="T03", vehicle_nickname=None, plate_number=None) for v in self._vins]

    def get_vehicle_status(self, lib_vehicle):
        self.calls.append(("get_vehicle_status", (lib_vehicle.vin,), {}, None))
        return _status()

    def __getattr__(self, name):
        def call(*a, **kw):
            self.calls.append((name, a, kw, self.operation_password))
            return {"code": 0}
        return call

    def commands(self):
        return [c for c in self.calls if not c[0].startswith(("get_", "login", "download"))]


class FakeHA:
    def __init__(self, error=False, language="en"):
        self.services, self.error, self._language = [], error, language

    def service(self, domain, service, data):
        if self.error:
            raise RuntimeError("notify gone")
        self.services.append((domain, service, data))

    def language(self):
        return self._language

    def time_zone(self):
        return "UTC"

    def integration_loaded(self):
        return True

    def events(self, *a):
        pass

    def admin_ids(self):
        return {"admin-id"}


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("LG_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("LG_STATE_DIR", str(tmp_path))
    return gw.Settings


def build(settings, monkeypatch, live=False, vehicles=(VIN,), pin="1234", ha=None, **env):
    if live:
        monkeypatch.setenv("LG_CLOUD_ENABLED", "true")
        monkeypatch.setenv("LG_DRY_RUN", "false")
    monkeypatch.setenv("LG_NOTIFY", "notify.mobile_app_phone")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    s = settings()
    store = ac.Store(s.state_dir)
    aid = store.add_account("leap@example.org", "secret-pw")
    for v in vehicles:
        store.report_vehicle(v, aid, name=f"Car {v[-2:]}", model="T03")
        if pin:
            store.update_vehicle(v, pin=pin)
    ha = ha or FakeHA()
    cloud = FakeCloud(vehicles)
    g = gw.Gateway(s, client_factory=lambda a: cloud, ha=ha)
    if live:
        g.clients[aid] = cloud
        for v in vehicles:
            g.vehicles[v].lib_vehicle = SimpleNamespace(vin=v, car_type="T03")
    g.cloud, g.aid, g.ha_test = cloud, aid, ha
    s.roles = {"admin-id": "full"}
    original = g.trigger
    g.trigger = lambda action, params=None, source="ha", user_id="admin-id", user_name="Alex", vin=None: \
        original(action, params, source, user_id, user_name, vin)
    g.trigger_raw = original
    return g


def approval_id(g):
    return next(iter(g.approvals.pending))


def answer(g, action, user="admin-id"):
    return g.approvals.answer({"data": {"action": action}, "context": {"user_id": user}})


def _status():
    from leapmotor_api import models as m
    return m.VehicleStatus(
        battery=m.BatteryStatus(soc=37, charge_state=m.ChargeState.CHARGING, battery_current=-17.0, battery_voltage=366.0,
                                ac_input_slow_charge=1, dc_input_fast_charge=0, charge_remain_time=55,
                                dump_energy=14050, expected_mileage=92),
        driving=m.DrivingStatus(speed=0, total_mileage=239, live_remaining_range=None, gear_status=0),
        location=m.LocationStatus(latitude=52.52, longitude=13.405),
        climate=m.ClimateStatus(ac_switch=False, outdoor_temp=15, ac_setting=22.0),
        doors=m.DoorStatus(driver_door_lock_status=True, bbcm_back_door_status=False),
        windows=m.WindowStatus(left_front_window_percent=0),
        tires=m.TirePressure(front_left_kpa=269, front_right_kpa=266, rear_left_kpa=272, rear_right_kpa=269),
        connectivity=m.ConnectivityStatus(), seat_comfort=m.SeatComfortStatus(), security=m.SecurityStatus(),
        ignition=m.IgnitionStatus(bcm_key_position_on3=False), collect_time=None, create_time=None, raw={})


# Allowlist
def test_defaults_are_safe(settings):
    s = settings()
    assert (s.cloud_enabled, s.dry_run) == (False, True)


@pytest.mark.parametrize("action", ["unlock", "open_trunk", "open_windows", "release_charging_cable"])
def test_opening_needs_approval(action):
    assert cmd.level(action, {}) == cmd.APPROVAL


@pytest.mark.parametrize("action", ["autopark", "piloted_parking", "set_speed_limit", "fota_install", "sentry_mode_off", "video", "unlock_vehicle"])
def test_dangerous_does_not_exist(action):
    assert action not in cmd.COMMANDS and cmd.level(action, {}) == cmd.BLOCKED


def test_check_levels():
    assert cmd.check_levels({"unlock": "blocked", " locate_vehicle ": "FREE"}) == {"unlock": "blocked", "locate_vehicle": "free"}
    with pytest.raises(ValueError):
        cmd.check_levels({"autopark": "free"})
    with pytest.raises(ValueError):
        cmd.check_levels({"unlock": "fre"})


# Safeguards
def test_dry_run_sends_nothing(settings, monkeypatch):
    monkeypatch.setenv("LG_CLOUD_ENABLED", "true")
    g = build(settings, monkeypatch)
    g.clients[g.aid] = g.cloud
    g.vehicles[VIN].lib_vehicle = SimpleNamespace(vin=VIN)
    r = g.trigger("lock")
    assert r["result"] == "dry run, not sent" and r["ok"] is True
    assert g.cloud.commands() == []


def test_cloud_off_sends_nothing(settings, monkeypatch):
    monkeypatch.setenv("LG_DRY_RUN", "false")
    g = build(settings, monkeypatch)
    r = g.trigger("lock")
    assert "cloud off" in r["result"] and r["ok"] is False
    assert g.cloud.commands() == []


def test_free_is_sent_pin_only_during_the_call(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    r = g.trigger("lock")
    assert r["result"] == "sent" and r["ok"] is True
    name, args, _, pin = g.cloud.commands()[0]
    assert (name, args[0], pin) == ("lock_vehicle", VIN, "1234")
    assert g.cloud.operation_password is None                     # gone again afterwards


def test_without_pin_sent_without_pin(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True, pin="")
    g.trigger("lock")
    assert g.cloud.commands()[0][3] is None


def test_climate_off_t03_with_payload(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    g.trigger("climate_off")
    name, _, kw, _ = g.cloud.commands()[0]
    assert name == "_remote_control" and json.loads(kw["cmd_content"])["operate"] == "off"
    assert json.loads(kw["cmd_content"])["temperature"] == "22"                  # default 22 °C


def test_climate_off_t03_with_preset_temperature(settings, monkeypatch):
    """Set the target without climate on: the temperature in the off payload is the chosen one."""
    g = build(settings, monkeypatch, live=True)
    r = g.trigger("climate_off", {"temperature": 40})
    assert r["result"].startswith("invalid") and r["ok"] is False and g.cloud.commands() == []
    assert g.trigger("climate_off", {"temperature": "21"})["result"] == "sent"     # invalid does not count for the cooldown
    name, _, kw, _ = g.cloud.commands()[0]
    payload = json.loads(kw["cmd_content"])
    assert (payload["operate"], payload["temperature"]) == ("off", "21")


def test_climate_on_payload_and_check(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    g.trigger("climate_on", {"mode": "hot", "temperature": 22, "fan_speed": 3, "recirculation": True})
    assert g.cloud.commands()[0][2]["params"] == {"circle": "in", "mode": "hot", "operate": "manual", "position": "all",
                                                   "temperature": "22", "windlevel": "3", "wshld": "1"}
    g._last.clear()
    assert "invalid" in g.trigger("climate_on", {"temperature": 40})["result"]


def test_cooldown_per_vehicle_and_hourly_limit(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True, vehicles=(VIN, VIN2))
    g.trigger("lock", vin=VIN)
    assert g.trigger("lock", vin=VIN)["result"] == "repeated too fast"
    assert g.trigger("lock", vin=VIN2)["result"] == "sent"     # other car
    g.s.max_per_hour = 3
    g.trigger("quick_heat", vin=VIN)
    assert g.trigger("quick_cool", vin=VIN)["result"] == "hourly limit reached"


def test_blocked_in_the_app(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    g.s.ui_apply({"levels": {"quick_heat": "blocked"}})
    r = g.trigger("quick_heat")
    assert r["result"] == "blocked" and r["ok"] is False and g.cloud.commands() == []


# Several vehicles
def test_without_vin_two_cars_nothing(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True, vehicles=(VIN, VIN2))
    assert "ambiguous" in g.trigger("lock")["result"]
    assert "ambiguous" in g.trigger("lock", vin="WVWZZZ1JZXW000001")["result"]
    assert g.cloud.commands() == []


def test_command_hits_only_the_chosen_car(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True, vehicles=(VIN, VIN2))
    g.store.update_vehicle(VIN2, pin="9876")
    g.trigger("lock", vin=VIN2)
    name, args, _, pin = g.cloud.commands()[0]
    assert args[0] == VIN2 and pin == "9876"


def test_poll_several_cars(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True, vehicles=(VIN, VIN2))
    g.clients.clear()
    for v in (VIN, VIN2):
        g.vehicles[v].lib_vehicle = None
    for v in (VIN, VIN2):
        assert g.poll(v)["battery"] == 37
    assert {c[1][0] for c in g.cloud.calls if c[0] == "get_vehicle_status"} == {VIN, VIN2}
    assert sum(1 for c in g.cloud.calls if c[0] == "login") == 1           # one account, one login
    d = g.vehicle_data(VIN2)
    assert d["name"] == "Car 99" and d["pin_set"] is True and "pin" not in d
    assert set(d) == {"vin", "name", "model", "enabled", "pin_set", "status", "last_poll", "values", "extras", "image_hash",
                      "last_command", "approvals_pending", "trip_end_min", "plate", "trip_computer"}


def test_new_vehicle_from_list(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True, vehicles=(VIN,))
    g.cloud._vins = (VIN, VIN2)
    assert set(g.vehicle_list(g.aid)) == {VIN, VIN2}
    new = g.store.vehicle(VIN2)
    assert new["enabled"] is True and new["pin"] == ""                     # read yes, commands without PIN no


# Accounts, PIN, certificate
def test_store_gives_out_no_secrets(tmp_path):
    store = ac.Store(str(tmp_path))
    aid = store.add_account("a@b.de", "pw123")
    store.report_vehicle(VIN, aid, model="T03")
    store.update_vehicle(VIN, pin="4711")
    text = json.dumps(store.public())
    assert "pw123" not in text and "4711" not in text and '"pin_set": true' in text
    assert oct(os.stat(tmp_path / "accounts.json").st_mode)[-3:] == "600"
    assert ac.Store(str(tmp_path)).vehicle(VIN)["pin"] == "4711"            # survives a restart


@pytest.mark.parametrize("wrong", [dict(pin="12"), dict(pin="abcd"), dict(name=""), dict(name="x" * 41)])
def test_vehicle_input_checked(tmp_path, wrong):
    store = ac.Store(str(tmp_path))
    store.report_vehicle(VIN, store.add_account("a@b.de", "pw"), model="T03")
    with pytest.raises(ValueError):
        store.update_vehicle(VIN, **wrong)


def test_account_duplicate_and_invalid(tmp_path):
    store = ac.Store(str(tmp_path))
    store.add_account("a@b.de", "pw")
    with pytest.raises(ValueError):
        store.add_account("A@B.de", "pw2")
    with pytest.raises(ValueError):
        store.add_account("no-at", "pw")
    with pytest.raises(ValueError):
        store.report_vehicle("SHORT", "x")


def _pair():
    from datetime import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "test")])
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(1).not_valid_before(datetime(2026, 1, 1)).not_valid_after(datetime(2030, 1, 1))
         .sign(k, hashes.SHA256()))
    return (c.public_bytes(serialization.Encoding.PEM),
            k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def test_certificate_checked_and_saved(tmp_path):
    cert, key = _pair()
    info = ac.save_certificate(str(tmp_path), cert, key)
    assert info["present"] and info["valid_until"].startswith("2030")
    assert oct(os.stat(tmp_path / "app_key.pem").st_mode)[-3:] == "600"
    _, other_key = _pair()
    with pytest.raises(ValueError, match="does not match"):
        ac.save_certificate(str(tmp_path), cert, other_key)
    assert (tmp_path / "app_key.pem").read_bytes() == key                    # nothing overwritten
    with pytest.raises(ValueError):
        ac.save_certificate(str(tmp_path), b"no pem", key)


# Approval
def test_unlock_only_after_approval(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    r = g.trigger("unlock")
    assert r["result"] == "waiting for approval" and r["ok"] is True and g.cloud.commands() == []
    domain, service, data = g.ha_test.services[0]
    assert (domain, service) == ("notify", "mobile_app_phone") and data["title"] == "Car 47: approval needed"
    yes = data["data"]["actions"][0]
    assert yes["authenticationRequired"] is True and yes["action"].startswith("LMGW_YES_") and yes["title"] == "Approve"
    assert answer(g, yes["action"]) == "approved"
    name, args, _, pin = g.cloud.commands()[0]
    assert (name, args[0], pin) == ("unlock_vehicle", VIN, "1234")
    assert g.log.recent(1)[0]["result"] == "sent" and g.log.recent(1)[0]["ok"] is True


def test_push_follows_home_assistant_language(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True, ha=FakeHA(language="de"))
    g.trigger("unlock")
    data = g.ha_test.services[0][2]
    assert data["title"] == "Car 47: Freigabe nötig" and "Alex: Entriegeln" in data["message"]
    assert [a["title"] for a in data["data"]["actions"]] == ["Freigeben", "Ablehnen"]


def test_reject(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    g.trigger("open_trunk")
    aid = approval_id(g)
    assert answer(g, f"LMGW_NO_{aid}") == "rejected"
    entry = g.log.recent(1)[0]
    assert g.cloud.commands() == [] and entry["result"] == "rejected" and entry["ok"] is False


def test_approval_only_once_and_not_guessable(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    g.trigger("unlock")
    aid = approval_id(g)
    assert len(aid) == 16
    assert answer(g, "LMGW_YES_0000000000000000") == "unknown"
    answer(g, f"LMGW_YES_{aid}")
    assert answer(g, f"LMGW_YES_{aid}") == "unknown"
    assert [c[0] for c in g.cloud.commands()] == ["unlock_vehicle"]


def test_wrong_user(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    g.trigger("unlock")
    aid = approval_id(g)
    assert answer(g, f"LMGW_YES_{aid}", user="someone") == "wrong user"
    assert g.cloud.commands() == []


def test_expired_and_cleanup(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    g.trigger("unlock")
    aid = approval_id(g)
    g.approvals.pending[aid]["expires"] = time.time() - 1
    assert answer(g, f"LMGW_YES_{aid}") == "expired"
    g._last.clear()
    g.trigger("unlock")
    next(iter(g.approvals.pending.values()))["expires"] = 0
    g.approvals.cleanup()
    assert g.approvals.pending == {} and g.cloud.commands() == []


def test_approval_not_deliverable(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    g.ha = g.approvals.ha = FakeHA(error=True)
    r = g.trigger("unlock")
    assert r["result"].startswith("approval not deliverable") and r["ok"] is False and g.cloud.commands() == []


def test_several_devices_and_cleanup(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True, LG_NOTIFY="notify.mobile_app_phone,notify.mobile_app_tablet")
    g.s.roles["sam-id"] = "full"                       # no admin, but allowed to unlock
    g.trigger("unlock")
    aid = approval_id(g)
    assert answer(g, f"LMGW_YES_{aid}", user="sam-id") == "approved"
    assert len([d for d in g.ha_test.services if d[2].get("message") == "clear_notification"]) == 2


def test_approval_only_admins_and_id_stays_secret(settings, monkeypatch):
    """By default only an admin may approve. Another user does not cancel the approval either.
    The id appears nowhere Home Assistant can see it (last_command goes to every user as an attribute)."""
    g = build(settings, monkeypatch, live=True)
    g.trigger("unlock")
    aid = approval_id(g)
    for other in ({}, {"context": {}}, {"context": {"user_id": "guest-id"}}):
        assert g.approvals.answer({"data": {"action": f"LMGW_YES_{aid}"}, **other}) == "wrong user"
    assert aid in g.approvals.pending and g.cloud.commands() == []                 # still waiting
    data = json.dumps(g.vehicle_data(VIN), default=str)
    assert aid not in data and aid not in json.dumps(g.log.recent(50))
    for broken in ("LMGW_", "LMGW_YES", "LMGW_YES_", "LMGW_YES_xyz", f"LMGW_MAYBE_{aid}", f"LMGW_JA_{aid}"):
        assert answer(g, broken) == "invalid"
    assert answer(g, f"LMGW_YES_{aid}") == "approved"
    assert g.log.recent(1)[0]["approval"] == gw.approval_ref(aid)


# Roles
def test_roles(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    g.s.roles = {"admin-id": "full", "kim-id": "read", "sam-id": "limited"}
    raw = g.trigger_raw
    assert "no permission" in raw("lock", user_id="kim-id")["result"]
    assert raw("lock", user_id="sam-id")["result"] == "sent"
    assert "no permission" in raw("unlock", user_id="sam-id")["result"]
    assert "no permission" in raw("unlock", user_id=None)["result"]
    assert raw("unlock", user_id="admin-id", user_name="Alex")["result"] == "waiting for approval"
    assert "Alex: Unlock" in g.ha_test.services[0][2]["message"]
    assert all(c[0] != "unlock_vehicle" for c in g.cloud.commands())


def test_log_without_secrets(settings, monkeypatch):
    from fastapi.testclient import TestClient
    g = build(settings, monkeypatch, live=True)
    gw.gateway = g
    c = TestClient(gw.app, client=("172.30.32.2", 1), headers={"X-Remote-User-Id": "admin-id", "X-Remote-User-Name": "alex"})
    assert c.put(f"/api/vehicles/{VIN}", json={"pin": "5555", "name": "Small"}).status_code == 200
    assert c.post("/api/accounts", json={"username": "two@b.de", "password": "pw-two"}).status_code == 200
    raw = open(g.log.path).read()
    assert "5555" not in raw and "pw-two" not in raw and '"set"' in raw
    a = c.get("/api/accounts").json()
    assert "pw-two" not in json.dumps(a) and "5555" not in json.dumps(a)
    assert {x["username"] for x in a["accounts"]} == {"leap@example.org", "two@b.de"}
    assert c.put(f"/api/vehicles/{VIN}", json={"pin": "12"}).status_code == 422


# Settings
def test_settings_checked_and_saved(settings):
    s = settings()
    v = s.ui_apply({"notify_targets": ["notify.mobile_app_phone", "notify.mobile_app_tablet"], "approval_timeout_s": 300,
                    "levels": {"locate_vehicle": "free", "unlock": "blocked"}, "poll_idle_min": 30,
                    "roles": {"admin-id": "full"}})
    assert v["levels"]["unlock"] == "blocked" and v["levels"]["lock"] == "free"
    with open(s.ui_path, encoding="utf-8") as fh:            # stored: only the deviations
        assert json.load(fh)["levels"] == {"locate_vehicle": "free", "unlock": "blocked"}
    assert os.path.basename(s.ui_path) == "settings.json"
    new = settings()
    assert new.notify_targets[1] == "notify.mobile_app_tablet" and new.role("admin-id") == "full" and new.poll_idle == 1800


@pytest.mark.parametrize("wrong", [{"notify_targets": []}, {"notify_targets": ["light.x"]}, {"approval_timeout_s": 5},
                                   {"trip_end_min": 0}, {"trip_end_min": 61},
                                   {"levels": {"autopark": "free"}}, {"levels": "unlock=free"}, {"pin": "1234"},
                                   {"dry_run": False}, {"roles": {"x": "admin"}}])
def test_invalid_settings_change_nothing(settings, wrong):
    s = settings()
    before = s.ui_values()
    with pytest.raises(ValueError):
        s.ui_apply(wrong)
    assert s.ui_values() == before and s.dry_run is True


def test_obsolete_settings_file_starts(settings):
    """Fields that no longer exist and commands that are gone do not stop the start."""
    s = settings()
    with open(s.ui_path, "w", encoding="utf-8") as fh:
        json.dump({"approver": ["x"], "notify_targets": ["notify.mobile_app_phone"], "approval_timeout_s": 200,
                   "roles": {"admin-id": "full"}, "levels": {"unlock": "blocked", "lock": "free", "gone": "free"}}, fh)
    new = settings()
    assert new.approval_timeout_s == 200 and new.role("admin-id") == "full"
    assert new.ui_values()["levels"]["unlock"] == "blocked" and "approver" not in new.ui_values()
    with pytest.raises(ValueError):                         # through the app, unknown stays an error
        new.ui_apply({"approver": ["x"]})


# API
def test_v2_only_from_core_with_token(settings, monkeypatch):
    from fastapi.testclient import TestClient
    g = build(settings, monkeypatch, live=True, vehicles=(VIN, VIN2))
    gw.gateway = g
    h = {"Authorization": f"Bearer {g.token()}"}
    core = TestClient(gw.app, client=("172.30.32.1", 1))
    assert core.get("/api/v2/vehicles").status_code == 401
    assert TestClient(gw.app, client=("172.30.32.2", 1)).get("/api/v2/vehicles", headers=h).status_code == 401
    assert TestClient(gw.app, client=("172.30.33.7", 1)).get("/api/v2/vehicles", headers=h).status_code == 401
    d = core.get("/api/v2/vehicles", headers=h).json()
    assert set(d["vehicles"]) == {VIN, VIN2}
    assert {"status", "cloud_enabled", "dry_run", "certificate", "levels", "approval_push", "approvals_pending",
            "commands"} <= set(d["gateway"])
    assert d["gateway"]["commands"]["unlock"] == {"title": "Unlock", "icon": "mdi:car-key", "button": False, "level": "approval"}
    assert "1234" not in json.dumps(d)
    r = core.post("/api/v2/command", headers=h, json={"action": "lock", "vin": VIN2, "user_id": "admin-id"}).json()
    assert r["result"] == "sent" and r["ok"] is True and r["vin"] == VIN2
    assert "ambiguous" in core.post("/api/v2/command", headers=h, json={"action": "lock"}).json()["result"]
    assert core.get("/api/v1/fahrzeuge", headers=h).status_code == 401         # v1 is gone


def test_app_page_only_through_ingress(settings, monkeypatch):
    from fastapi.testclient import TestClient
    gw.gateway = build(settings, monkeypatch)
    assert TestClient(gw.app, client=("203.0.113.50", 1)).get("/api/status").status_code == 401
    assert TestClient(gw.app, client=("172.30.33.7", 1)).get("/api/status").status_code == 401
    assert TestClient(gw.app, client=("203.0.113.50", 1)).get("/health").status_code == 200
    s = TestClient(gw.app, client=("172.30.32.2", 1), headers={"X-Remote-User-Id": "admin-id"}).get("/api/status").json()
    assert s["dry_run"] is True and s["vehicles"][0]["vin"] == VIN


def test_interface_only_for_admins(settings, monkeypatch):
    """Home Assistant gives every user an ingress session. The interface has to check itself
    whether an admin is behind it."""
    from fastapi.testclient import TestClient
    g = build(settings, monkeypatch)
    gw.gateway = g
    ingress = lambda **h: TestClient(gw.app, client=("172.30.32.2", 1), headers=h)
    assert ingress().get("/api/status").status_code == 403                                   # without header
    assert ingress(**{"X-Remote-User-Id": "kim-id"}).get("/api/status").status_code == 403    # no admin
    assert ingress(**{"X-Remote-User-Id": "kim-id"}).put("/api/settings", json={"roles": {"kim-id": "full"}}).status_code == 403
    assert g.s.role("kim-id") == "limited"
    assert ingress(**{"X-Remote-User-Id": "kim-id"}).get("/").status_code == 403
    assert ingress(**{"X-Remote-User-Id": "admin-id"}).get("/api/status").status_code == 200


def test_admin_list_unavailable_means_closed(settings, monkeypatch):
    from fastapi.testclient import TestClient
    g = build(settings, monkeypatch)

    def broken():
        raise RuntimeError("Home Assistant gone")
    g.ha.admin_ids = broken
    gw.gateway = g
    c = TestClient(gw.app, client=("172.30.32.2", 1), headers={"X-Remote-User-Id": "admin-id"})
    assert c.get("/api/status").status_code == 403


def test_push_targets_only_against_real_list(settings, monkeypatch):
    from fastapi.testclient import TestClient
    g = build(settings, monkeypatch)
    gw.gateway = g
    monkeypatch.setattr(gw, "_ha_choices", lambda: {"notify": [], "users": []})
    c = TestClient(gw.app, client=("172.30.32.2", 1), headers={"X-Remote-User-Id": "admin-id"})
    assert c.put("/api/settings", json={"notify_targets": ["notify.mobile_app_kim"]}).status_code == 503
    assert g.s.notify_targets == ["notify.mobile_app_phone"]


# Secrets
def test_redact(settings, monkeypatch):
    g = build(settings, monkeypatch)
    text = g.redact("login failed for leap@example.org pw secret-pw, operatePassword=1234 pin: 1234 token='abc'")
    assert "secret-pw" not in text and "1234" not in text and "abc" not in text


def test_library_error_with_pin_is_redacted(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)

    def echo(*a, **kw):
        raise RuntimeError("remote control failed: operatePassword=1234 body=...")
    g.cloud.lock_vehicle = echo
    r = g.trigger("lock")
    assert r["result"].startswith("error") and "1234" not in r["result"] and r["ok"] is False
    assert "1234" not in open(g.log.path).read()


def test_rejected_login_is_not_repeated(settings, monkeypatch):
    from leapmotor_api.exceptions import LeapmotorAuthError
    g = build(settings, monkeypatch, live=True)
    g.clients.clear()
    attempts = []

    class Refusing(FakeCloud):
        def login(self):
            attempts.append(1)
            raise LeapmotorAuthError("wrong password")
    g._client_factory = lambda a: Refusing()
    for _ in range(3):
        with pytest.raises(Exception):
            g._client(g.aid)
    assert len(attempts) == 1 and "password" in g.login_refused[g.aid]
    g.store.set_password(g.aid, "new")
    g.allow_after_change(g.aid)
    g._client_factory = lambda a: g.cloud
    assert g._client(g.aid) is g.cloud


# Rebuilt requests (today, total energy)
class SigningCloud:
    sign_key, device_id, language, account_cert = b"0123456789abcdef", "device", "en-GB", ("c", "k")

    def __init__(self, reply):
        self.reply, self.post = reply, []

    def _auth_headers(self):
        return {"X-Token": "t"}

    def _post(self, **kw):
        self.post.append(kw)
        return {"status_code": 200, "body": json.dumps(self.reply)}

    def _parse_api_body(self, code, body, name):
        return json.loads(body)


def test_today_and_total_energy():
    c = SigningCloud({"data": {"driverEC": 1.5, "acEC": 0.3, "otherEC": 0.2, "totalEnergy": 39.5}})
    lib_vehicle = SimpleNamespace(vin=VIN)
    h = gw._today_breakdown(c, lib_vehicle)
    assert (h.driver_ec, h.ac_ec, h.other_ec) == (1.5, 0.3, 0.2)
    assert c.post[0]["path"].endswith("getLastweekEC") and f"carvin={VIN}" in c.post[0]["data"]
    d = gw._distance_energy_7_days(c, lib_vehicle)
    assert d["data"]["totalEnergy"] == 39.5 and "begintime=" in c.post[1]["data"] and c.post[1]["headers"]["X-Token"] == "t"


def test_token_stays_and_is_private(settings, monkeypatch):
    g = build(settings, monkeypatch)
    assert g.token() == g.token() and len(g.token()) >= 40
    assert oct(os.stat(os.path.join(g.s.state_dir, "integration_token")).st_mode)[-3:] == "600"


# State
def test_values_from_library_model():
    v = st.values(_status())
    assert (v["battery"], v["odometer"], v["range"], v["energy"], v["tyre_rl"]) == (37, 239, 92, 14.05, 2.72)
    assert v["charging"] is True and v["gear"] == "park" and st.active(v) is True
    assert not any(k for k in v if any(c in k for c in "äöü")) and "akku" not in v


@pytest.mark.parametrize("raw, mode", [(0, "fan"), (1, "cool"), (2, "heat"), (None, None)])
def test_climate_mode_names(raw, mode):
    assert st._name(raw, st.CLIMATE_MODES) == mode


def test_image_key():
    assert gw._find_key({"data": {"carType": "T03", "pictureKey": "abc"}}) == "abc"
    assert gw._find_key({"data": [{"x": 1}, {"key": "k2"}]}) == "k2"
    assert gw._find_key({"data": {}}) is None


def test_library_logs_no_pin(caplog):
    """The line leapmotor_api writes for every remote command."""
    import logging
    import gateway  # noqa: F401  (installs the redacting record factory)
    with caplog.at_level(logging.INFO):
        logging.getLogger("leapmotor_api.client").info(
            "Leapmotor remote ctl request body for %s: %s", "charge_stop",
            "cmdContent=%7B%22value%22%3A%22stop%22%7D&vin=LFZ00000000000047&cmdId=193&operatePassword=SECRET%3D%3D")
        logging.getLogger("other").warning("login {'password': 'Plaintext1', 'token': 'abc123'}")
    assert "SECRET" not in caplog.text and "operatePassword=***" in caplog.text
    assert "Plaintext1" not in caplog.text and "abc123" not in caplog.text
    assert "vin=LFZ00000000000047" in caplog.text          # the rest stays readable


def test_vehicle_time_without_zone_is_utc(monkeypatch):
    """collectTime from the API text is UTC. It must not be read as local time."""
    from datetime import datetime, timedelta, timezone
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        a_minute_ago = (now - timedelta(minutes=1)).replace(tzinfo=None)          # as the API delivers it
        assert st.to_utc(a_minute_ago) == (now - timedelta(minutes=1)).isoformat()
        local = (now - timedelta(minutes=1)).astimezone().replace(tzinfo=None)     # as the library derives it from "sts"
        assert st.to_utc(local) == (now - timedelta(minutes=1)).isoformat()
        assert st.to_utc(1791392000000) == datetime.fromtimestamp(1791392000, timezone.utc).isoformat()
        assert st.to_utc(None) is None
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_trip_end_setting_and_afterrun(settings, monkeypatch):
    """After parking the fast rate stays on for trip_end_min, so continuing is noticed at once."""
    g = build(settings, monkeypatch, live=True)
    assert g.s.trip_end_min == 10 and g.vehicle_data(VIN)["trip_end_min"] == 10
    assert g.s.ui_apply({"trip_end_min": 3})["trip_end_min"] == 3
    assert settings().trip_end_min == 3                                  # stored
    r = g.vehicles[VIN]
    idle = {"ready": False, "locked": True}
    clock = [1000.0]
    monkeypatch.setattr(gw.time, "time", lambda: clock[0])
    assert g.poll_interval(r, {**idle, "ready": True}) == g.s.poll_active          # driving
    assert g.poll_interval(r, idle) == g.s.poll_active                               # just parked: afterrun
    clock[0] += 2 * 60
    assert g.poll_interval(r, idle) == g.s.poll_active                               # 2 min < 3 min
    clock[0] += 2 * 60
    assert g.poll_interval(r, idle) == g.s.poll_idle                                 # 4 min > 3 min: idle
    assert g.poll_interval(r, {**idle, "ready": True}) == g.s.poll_active and r.ready_off_since is None


def test_approval_without_push(settings, monkeypatch):
    """Approval by push can be switched off. Then the role full is enough, all others stay out."""
    g = build(settings, monkeypatch, live=True)
    with pytest.raises(ValueError):
        g.s.ui_apply({"notify_targets": []})                                 # with push a target is needed
    with pytest.raises(ValueError):
        g.s.ui_apply({"approval_push": "no"})
    assert g.s.ui_apply({"approval_push": False, "notify_targets": []})["approval_push"] is False
    assert settings().approval_push is False                                 # stored
    r = g.trigger("open_trunk")                                              # role full
    assert r["result"] == "sent" and g.ha_test.services == []                # without push
    r = g.trigger_raw("open_windows", None, "ha", "guest-id", "Guest", None)
    assert r["result"] == "no permission (role limited)"
    assert g.trigger_raw("open_windows", None, "ha", None, "", None)["result"].startswith("no permission")   # automation
    g.s.ui_apply({"approval_push": True, "notify_targets": ["notify.mobile_app_phone"]})
    assert g.trigger("open_windows")["result"] == "waiting for approval"


def test_refresh_limited(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    assert g.trigger("refresh") == {"result": "refresh requested", "ok": True, "vin": VIN}
    assert g.trigger("refresh")["result"] == "repeated too fast"
    g.s.roles["guest-id"] = "read"
    g._refresh_last.clear()
    r = g.trigger("refresh", user_id="guest-id")
    assert "no permission" in r["result"] and r["ok"] is False


def test_loop_survives_errors(settings, monkeypatch):
    g = build(settings, monkeypatch, live=True)
    runs = []

    def one_pass():
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("broken")
        g._stop.set()
    monkeypatch.setattr(g, "_pass", one_pass)
    monkeypatch.setattr(g._stop, "wait", lambda t=None: None)
    g.loop()
    assert len(runs) == 2


def test_time_zone_and_language_from_home_assistant(settings, monkeypatch):
    s = settings()
    ha = gw.HA(s)

    class Reply:
        def __init__(self, tz):
            self.tz = tz

        def raise_for_status(self):
            pass

        def json(self):
            return {"time_zone": self.tz, "language": "de"}
    monkeypatch.setenv("TZ", "Europe/Lisbon")
    monkeypatch.setattr(gw.requests, "get", lambda url, **kw: Reply("Does/Not"))
    assert ha.time_zone() == "Europe/Lisbon"                       # nonsense from Home Assistant: container time zone
    monkeypatch.setattr(gw.requests, "get", lambda url, **kw: Reply("America/New_York"))
    assert ha.time_zone() == "America/New_York"
    monkeypatch.setattr(gw.requests, "get", lambda url, **kw: 1 / 0)
    assert ha.time_zone() == "America/New_York" and ha.language() == "de"     # kept


# Plate and image
@pytest.mark.parametrize("raw, normal", [("Mü AB12 E", "MÜ-AB 12E"), ("mü-ab 12e", "MÜ-AB 12E"), ("MÜ-AB 12E", "MÜ-AB 12E"),
                                         ("B-X 1", "B-X 1"), ("  ", ""), ("W-12345A", "W-12345A")])
def test_plate_normalized(raw, normal):
    assert pl.normalize(raw) == normal


@pytest.mark.parametrize("wrong", ["<b>", "A" * 13, "B/LM 1", "-AB"])
def test_plate_invalid(tmp_path, wrong):
    with pytest.raises(ValueError):
        pl.normalize(wrong)
    store = ac.Store(str(tmp_path))
    store.report_vehicle(VIN, store.add_account("a@b.de", "pw"), model="T03")
    with pytest.raises(ValueError):
        store.update_vehicle(VIN, plate=wrong)


def _test_image():
    """A "car" as in the Leapmotor image."""
    from PIL import Image, ImageDraw
    b = Image.new("RGBA", (400, 220), (0, 0, 0, 0))
    d = ImageDraw.Draw(b)
    d.rounded_rectangle((20, 30, 380, 200), radius=40, fill=(140, 200, 200, 255))
    out = io.BytesIO()
    b.save(out, format="PNG")
    return out.getvalue()


def _image(png):
    from PIL import Image
    return Image.open(io.BytesIO(png)).convert("RGBA")


def test_plate_in_the_data_not_in_the_image(settings, monkeypatch):
    from fastapi.testclient import TestClient
    g = build(settings, monkeypatch)
    r = g.vehicles[VIN]
    r.image_pack = SimpleNamespace(compose=lambda status=None: _test_image())
    g._build_image(g.aid, r, None)
    before = r.image_hash
    assert os.path.exists(os.path.join(g.s.state_dir, f"vehicle_{VIN}.png"))
    gw.gateway = g
    c = TestClient(gw.app, client=("172.30.32.2", 1), headers={"X-Remote-User-Id": "admin-id", "X-Remote-User-Name": "alex"})
    resp = c.put(f"/api/vehicles/{VIN}", json={"plate": "b-lm 2026e"})
    assert resp.status_code == 200 and resp.json()["plate"] == "B-LM 2026E"
    g._build_image(g.aid, r, None)
    assert r.image_hash == before                                                # the image stays as Leapmotor delivers it
    assert g.vehicle_data(VIN)["plate"] == "B-LM 2026E"
    assert c.put(f"/api/vehicles/{VIN}", json={"plate": "<b>"}).status_code == 422
    assert c.put(f"/api/vehicles/{VIN}", json={"plate": ""}).status_code == 200
    assert g.vehicle_data(VIN)["plate"] is None
    r.lib_vehicle = SimpleNamespace(vin=VIN, car_type="T03", plate_number="b lm 2026e")
    assert g.plate(VIN) == "B-LM 2026E"                                          # otherwise the one from the Leapmotor app


def test_trip_computer_in_the_poll(settings, monkeypatch):
    """Polling updates and stores the trip computer. The integration gets the summary."""
    g = build(settings, monkeypatch, live=True)
    g.poll(VIN)                                                                  # _status(): charging
    s = g.vehicle_data(VIN)["trip_computer"]
    assert s["charging_since"] and s["charge_count"] == 0
    assert os.path.exists(os.path.join(g.s.state_dir, f"trip_computer_{VIN}.json"))


def _with_edge_line(car_at_edge=False):
    """As in the T03 image pack: a semi transparent line along the full right edge, or a car up to the edge."""
    from PIL import ImageDraw
    b = _image(_test_image())
    if car_at_edge:
        ImageDraw.Draw(b).rectangle((300, 0, 399, 219), fill=(140, 200, 200, 255))
    else:
        ImageDraw.Draw(b).line((399, 0, 399, 219), fill=(212, 249, 255, 87))
        ImageDraw.Draw(b).line((398, 0, 398, 219), fill=(212, 249, 255, 7))     # almost invisible second column
    out = io.BytesIO()
    b.save(out, format="PNG")
    return out.getvalue()


def test_edge_line_removed_car_stays():
    import vehicle_image as vi
    clean = _image(vi.remove_edge_lines(_with_edge_line()))
    assert clean.getchannel("A").getbbox() == _image(_test_image()).getchannel("A").getbbox()   # only the car left
    assert vi.remove_edge_lines(_test_image()) == _test_image()                                   # unchanged without a line
    at_edge = _with_edge_line(car_at_edge=True)
    assert vi.remove_edge_lines(at_edge) == at_edge                                               # car at the edge stays
    assert vi.remove_edge_lines(b"broken") == b"broken"


def test_image_without_edge_line(settings, monkeypatch):
    g = build(settings, monkeypatch)
    r = g.vehicles[VIN]
    r.image_pack = SimpleNamespace(compose=lambda status=None: _with_edge_line())
    g._build_image(g.aid, r, None)
    stored = _image(open(g.image_path(VIN), "rb").read())
    assert stored.getpixel((399, 5))[3] == 0 and stored.getpixel((398, 5))[3] == 0


def test_startup_builds_the_gateway(settings, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(gw, "gateway", None)
    with TestClient(gw.app, client=("203.0.113.50", 1)) as c:
        assert c.get("/health").json() == {"ok": True}
        assert gw.gateway is not None and gw.gateway.status.startswith("cloud off")
    gw.gateway._stop.set()


# Demo mode
def test_demo_mode_runs_without_account_or_certificate(settings, monkeypatch, tmp_path):
    import demo
    monkeypatch.setenv("LG_DEMO_MODE", "true")
    s = settings()
    assert (s.cloud_enabled, s.dry_run) == (True, False)
    g = gw.Gateway(s, ha=FakeHA())
    values = g.poll(demo.DEMO_VIN)
    assert values["battery"] == 68 and values["locked"] is True and values["latitude"] == demo.HOME[0]
    data = g.vehicle_data(demo.DEMO_VIN)
    assert data["plate"] == demo.DEMO_PLATE and len(data["trip_computer"]["trips"]) == 8
    assert data["trip_computer"]["charges"][0]["type"] == "AC"
    g.s.roles["admin-id"] = "full"
    assert g.trigger("unlock", user_id="admin-id")["result"].startswith(("waiting for approval", "approval not deliverable"))
    assert g.trigger("lock", user_id="admin-id")["ok"] is True
    assert ("lock", {"locked": True}) in g._client(g.store.account_ids()[0]).commands
    # a second start keeps account and history
    g2 = gw.Gateway(settings(), ha=FakeHA())
    assert len(g2.store.account_ids()) == 1 and len(g2.vehicle_data(demo.DEMO_VIN)["trip_computer"]["trips"]) >= 8


def test_websocket_url_with_and_without_supervisor(settings, monkeypatch):
    monkeypatch.setenv("LG_HA_URL", "http://supervisor/core")
    assert gw.HA(settings()).ws_url() == "ws://supervisor/core/websocket"
    monkeypatch.setenv("LG_HA_URL", "http://172.31.99.10:8123/")
    assert gw.HA(settings()).ws_url() == "ws://172.31.99.10:8123/api/websocket"


def test_image_only_for_known_vehicles(settings, monkeypatch, tmp_path):
    g = build(settings, monkeypatch)
    (tmp_path / f"vehicle_{VIN}.png").write_bytes(b"png")
    (tmp_path / "secret.png").write_bytes(b"x")
    assert g.image_path(VIN).endswith(f"vehicle_{VIN}.png")
    for bad in ("../secret", "LFZ00000000000099", "", "/etc/passwd"):
        assert g.image_path(bad) is None


def test_announce_waits_until_the_integration_is_loaded(settings, monkeypatch):
    """Right after the first install Home Assistant does not know the integration yet. An early
    announcement would end in "Cannot find integration" in its log."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    ha = FakeHA()
    loaded = iter([False, False, True])
    ha.integration_loaded = lambda: next(loaded)
    g = build(settings, monkeypatch, ha=ha)
    posts = []
    monkeypatch.setattr(gw.requests, "post", lambda url, **kw: posts.append(url) or type("R", (), {"status_code": 200})())
    waits = []
    monkeypatch.setattr(g._stop, "wait", lambda t=None: waits.append(t) or (len(posts) == 0 and len(waits) > 5))
    g.announce_when_loaded()
    assert posts == ["http://supervisor/discovery"] and waits == [60, 60]


def test_integration_loaded_asks_for_the_manifest(settings, monkeypatch):
    h = gw.HA(settings())
    sent = []
    replies = iter([{"success": False, "error": {"code": "not_found"}}, {"success": True, "result": {}}])
    monkeypatch.setattr(h, "_ws_call", lambda m: sent.append(m) or next(replies))
    assert h.integration_loaded() is False and h.integration_loaded() is True
    assert sent[0] == {"type": "manifest/get", "integration": "leapmotor_gateway"}
