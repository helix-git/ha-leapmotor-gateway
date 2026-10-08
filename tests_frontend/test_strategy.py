"""The dashboard strategy in a real browser (Chromium via Playwright), without Home Assistant.
hass is simulated, the data has the shape of the WebSocket command leapmotor_gateway/dashboard.

    /root/pw-venv/bin/python -m pytest -q -p no:cacheprovider tests_frontend
"""
import pathlib

import pytest
from playwright.sync_api import sync_playwright

JS = pathlib.Path(__file__).parents[1] / "custom_components/leapmotor_gateway/frontend/leapmotor-gateway-strategy.js"
CARD = "leapmotor-trip-computer-card"
CORE_CARDS = {"heading", "tile", "conditional", "picture-entity", "picture-elements", "map", "markdown", f"custom:{CARD}"}
SWITCHABLE = {"button", "lock", "climate", "switch", "cover"}      # domains whose icon Home Assistant would toggle

SENSORS = ["battery", "range", "odometer", "energy", "outside_temp", "vehicle_time", "target_temp_left", "fan_speed",
           "window_fl", "window_fr", "window_rl", "window_rr", "sunshade", "charge_state", "charging_power",
           "charge_time_left", "tyre_fl", "tyre_fr", "tyre_rl", "tyre_rr", "plate", "status",
           "approvals_pending", "last_command", "state", "tyre_pressure", "trip_distance", "trip_duration", "trip_energy",
           "trip_consumption", "since_charge_distance", "since_charge_energy", "since_charge_consumption", "last_charge",
           "charges", "trips"]
BINARY = ["ready", "climate", "driver_door", "passenger_door", "rear_left_door", "rear_right_door", "trunk", "charging",
          "ac_cable", "dc_cable"]
COMMANDS = {"lock": ("Lock", "free", False), "unlock": ("Unlock", "approval", False),
            "climate_on": ("Climate on", "free", False), "climate_off": ("Climate off", "free", True),
            "close_windows": ("Close windows", "free", True),
            "open_windows": ("Open windows", "approval", True),
            "open_trunk": ("Open trunk", "blocked", True),
            "quick_heat": ("Quick heat", "free", True), "start_charging": ("Start charging", "approval", True),
            "locate_vehicle": ("Locate vehicle", "blocked", True)}
LOCAL = {"preset_temperature": "number", "preset_fan_speed": "number", "preset_mode": "select", "preset_recirculation": "switch"}


def vehicle(p, name, plate, with_climate=True):
    e = {k: {"entity_id": f"sensor.{p}_{k}", "name": k} for k in SENSORS}
    e.update({k: {"entity_id": f"binary_sensor.{p}_{k}", "name": k} for k in BINARY})
    e.update({"lock": {"entity_id": f"lock.{p}_lock", "name": "Lock state"},
              "image": {"entity_id": f"image.{p}_image", "name": "Vehicle image"},
              "location": {"entity_id": f"device_tracker.{p}_location", "name": "Location"},
              "button_refresh": {"entity_id": f"button.{p}_refresh", "name": "Refresh data"},
              "start_climate": {"entity_id": f"button.{p}_start_climate", "name": "Start climate"}})
    e.update({r: {"entity_id": f"{d}.{p}_{r}", "name": r} for r, d in LOCAL.items()})
    e.update({f"button_{n}": {"entity_id": f"button.{p}_{n}", "name": t} for n, (t, _, button) in COMMANDS.items() if button})
    if with_climate:
        e["climate_control"] = {"entity_id": f"climate.{p}_climate", "name": "Climate"}
    else:
        for k in ("climate", "target_temp_left", "fan_speed", "start_climate", *LOCAL):
            del e[k]
    return {"name": name, "plate": plate, "model": "T03", "entities": e}


def data(push=True, two=True):
    vehicles = [vehicle("t03", "Leapmotor T03", "XY-AB 123E")] + ([vehicle("c10", "C10", None, False)] if two else [])
    return {"vehicles": vehicles, "approval_push": push,
            "commands": {n: {"title": t, "icon": "mdi:car", "button": b, "level": lv} for n, (t, lv, b) in COMMANDS.items()}}


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        s = b.new_page()
        errors = []
        s.on("pageerror", lambda e: errors.append(str(e)))
        s.set_content("<!doctype html><title>Strategy</title>")
        s.add_script_tag(content=JS.read_text(encoding="utf-8"))
        # registers only after the app (see the comment in the strategy)
        assert s.evaluate('() => !customElements.get("ll-strategy-dashboard-leapmotor-gateway")')
        s.evaluate('() => customElements.define("home-assistant", class extends HTMLElement {})')
        assert s.evaluate('() => customElements.whenDefined("ll-strategy-dashboard-leapmotor-gateway").then(() => true)')
        assert not errors, errors
        yield s
        b.close()


def generate(page, d, lang="en", error=False):
    return page.evaluate("""async ([d, lang, error]) => {
        const S = customElements.get("ll-strategy-dashboard-leapmotor-gateway");
        const hass = {language: lang, locale: {language: lang},
                      callWS: async () => { if (error) throw new Error("gone"); return d; }};
        return await S.generate({}, hass);
    }""", [d, lang, error])


def cards(cfg):
    for v in cfg["views"]:
        for section in v.get("sections", []):
            yield from section["cards"]
        yield from v.get("cards", [])


def view(cfg, path):
    return next(v for v in cfg["views"] if v["path"] == path)


def triggers(obj):
    """Every action (tap, icon_tap, hold, double_tap) anywhere in the configuration."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.endswith("_action") and isinstance(v, dict):
                yield v
            yield from triggers(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from triggers(x)


def confirmations(cfg):
    return {a["target"]["entity_id"]: a["confirmation"]["text"] for a in triggers(cfg) if a.get("action") == "perform-action"}


def test_three_tabs_per_vehicle_and_tyre_page(page):
    cfg = generate(page, data())
    assert [v["path"] for v in cfg["views"]] == [
        "vehicle-1-trip-computer", "vehicle-1-status", "vehicle-1-climate", "vehicle-1-tyres",
        "vehicle-2-trip-computer", "vehicle-2-status", "vehicle-2-climate", "vehicle-2-tyres"]
    assert cfg["views"][0]["title"] == "Leapmotor T03 · Trip computer" and view(cfg, "vehicle-1-tyres")["subview"]
    single = generate(page, data(two=False))
    assert [v["title"] for v in single["views"]][:3] == ["Trip computer", "Status", "Climate"]
    everything = list(cards(cfg))
    assert all(isinstance(k, dict) for k in everything) and {k["type"] for k in everything} <= CORE_CARDS
    header = view(cfg, "vehicle-1-status")["sections"][0]["cards"][0]
    image = view(cfg, "vehicle-1-status")["sections"][0]["cards"][1]          # hidden while unavailable
    assert image["type"] == "conditional" and image["card"]["type"] == "picture-entity"
    assert image["conditions"] == [{"condition": "state", "entity": image["card"]["entity"], "state_not": "unavailable"}]
    assert [b["entity"] for b in header["badges"]] == ["sensor.t03_plate", "sensor.t03_state", "sensor.t03_battery",
                                                      "sensor.t03_range"]


def test_trip_computer_with_lists(page):
    tc = view(generate(page, data()), "vehicle-1-trip-computer")
    own = [k for k in cards({"views": [tc]}) if k["type"] == f"custom:{CARD}"]
    assert [k["part"] for k in own] == ["overview", "trips", "charges"]
    assert own[0]["entities"]["trip_distance"] == "sensor.t03_trip_distance" and own[2]["entities"]["charges"] == "sensor.t03_charges"


def _card(page, part, lang="en", element=CARD, legacy=False):
    """Render the card with a simulated hass. Returns the text of the card."""
    return page.evaluate("""([part, lang, element, legacy]) => {
        const st = (s, a = {}) => ({state: String(s), attributes: a, last_updated: "1"});
        const trips = Array.from({length: 10}, (_, i) => ({start: `2026-10-0${(i % 7) + 1}T05:42:00+00:00`, km: 8 + i, h: 0.5, kwh: 1.48, consumption: 18.5,
                                                           backfilled: i === 9, ...(i === 9 ? {h: null} : {})}));
        const states = {"sensor.t03_battery": st(75), "sensor.t03_range": st(185), "sensor.t03_energy": st(28.1),
          "sensor.t03_state": st("parked"), "sensor.t03_trip_distance": st(4), "sensor.t03_trip_duration": st(14), "sensor.t03_trip_consumption": st(12),
          "sensor.t03_since_charge_distance": st(1), "sensor.t03_since_charge_energy": st(0.1), "sensor.t03_since_charge_consumption": st(8),
          "sensor.t03_odometer": st(254), "sensor.t03_today_driving_percent": st(50), "sensor.t03_today_climate_percent": st(45),
          "sensor.t03_today_other_percent": st(5), "sensor.t03_trips": st(10, {items: trips}),
          "sensor.t03_charges": st(1, {items: [{start: "2026-10-07T14:02:43+00:00", end: "2026-10-07T16:38:13+00:00", charged_kwh: 17.56,
                                                soc_from: 28, soc_to: 75, type: "AC", place: "Charger <Station Road>"}]})};
        let roles = Object.fromEntries(Object.keys(states).map(k => [k.split(".t03_")[1], k]));
        let config = {part, entities: roles};
        if (legacy) {   // configuration as the card had it before the English port
          const old = {battery: "akku", range: "reichweite", energy: "energie", state: "zustand", trip_distance: "fahrt_km",
                       trip_duration: "fahrt_dauer", trip_consumption: "fahrt_verbrauch", trips: "fahrten", charges: "ladungen"};
          const parts = {overview: "uebersicht", trips: "fahrten", charges: "ladungen"};
          config = {teil: parts[part], entitaeten: Object.fromEntries(Object.entries(roles).map(([r, e]) => [old[r] || r, e]))};
        }
        const K = customElements.get(element);
        const k = new K(); k.setConfig(config);
        k.hass = {states, language: lang, locale: {language: lang}, formatEntityState: s => s.state === "parked" ? "Parked" : s.state};
        document.body.appendChild(k);
        const text = () => k.shadowRoot.querySelector("ha-card").innerText.replace(/\\u00ad/g, "").replace(/\\s+/g, " ");
        const t = text();
        if (part === "trips") { k.shadowRoot.querySelector('button[data-p="2"]').click(); return t + " || " + text(); }
        return t + (k.shadowRoot.innerHTML.includes("<Station Road>") ? " UNESCAPED" : "");
    }""", [part, lang, element, legacy])


def test_trip_computer_card_renders(page):
    o = _card(page, "overview")
    for part in ("Battery 75 %", "Range 185 km", "Energy in battery 28.1 kWh", "Parked", "Last trip", "4.0 km", "14 min",
                 "12.0 kWh/100 km", "Energy shares today", "Driving 50.0 %", "Climate 45.0 %", "Since last charge", "Odometer", "254 km"):
        assert part in o, part
    t = _card(page, "trips")
    before, after = t.split(" || ")
    assert "Page 1 of 2 · 10 Trips" in before and "Page 2 of 2" in after and "* added later" in after
    assert "* 17.0 km – 1.48" in after                                   # missed trip: driving time "–"
    ch = _card(page, "charges")
    assert "17.6 kWh" in ch and "28 → 75 %" in ch and "Charger <Station Road>" in ch and "UNESCAPED" not in ch


def test_trip_computer_card_in_german(page):
    o = _card(page, "overview", "de")
    assert "Akku 75 %" in o and "Energie im Akku 28,1 kWh" in o and "Energieanteile heute" in o
    assert "Seite 1 von 2 · 10 Fahrten" in _card(page, "trips", "de")


def test_legacy_card_name_and_config(page):
    """Dashboards from before the English port use leapmotor-bordcomputer-card with teil/entitaeten."""
    o = _card(page, "overview", element="leapmotor-bordcomputer-card", legacy=True)
    assert "Battery 75 %" in o and "4.0 km" in o and "Parked" in o
    assert "Page 1 of 2 · 10 Trips" in _card(page, "trips", element="leapmotor-bordcomputer-card", legacy=True)
    assert page.evaluate(f'() => window.customCards.filter(c => c.type === "{CARD}").length') == 1


def test_tyre_pressure_opens_top_view(page):
    cfg = generate(page, data())
    tile = next(k for k in cards({"views": [view(cfg, "vehicle-1-status")]}) if k.get("entity") == "sensor.t03_tyre_pressure")
    assert tile["tap_action"]["action"] == "navigate" and tile["tap_action"]["navigation_path"].endswith("/vehicle-1-tyres")
    image = next(k for k in cards({"views": [view(cfg, "vehicle-1-tyres")]}) if k["type"] == "picture-elements")
    assert image["image"].endswith("top-view.svg")
    assert [e["entity"] for e in image["elements"]] == [f"sensor.t03_tyre_{x}" for x in ("fl", "fr", "rl", "rr")]


def test_climate_preset_first_then_start(page):
    cl = view(generate(page, data()), "vehicle-1-climate")
    tiles = {k.get("entity"): k for k in cards({"views": [cl]})}
    assert tiles["number.t03_preset_temperature"]["features"][0]["type"] == "numeric-input"
    assert tiles["select.t03_preset_mode"]["features"][0]["type"] == "select-options"
    assert tiles["switch.t03_preset_recirculation"]["tap_action"]["action"] == "toggle"   # local: a tap toggles directly
    texts = confirmations({"views": [cl]})
    assert texts["button.t03_start_climate"].startswith("Start climate") and "button.t03_climate_off" in texts
    assert "button.t03_quick_heat" in texts


def test_every_action_asks_first(page):
    cfg = generate(page, data())
    local = {e["entity_id"] for v in data()["vehicles"] for r, e in v["entities"].items() if r in LOCAL}
    actions = [a for a in triggers(cfg) if a.get("action") == "perform-action"]
    assert len(actions) >= 12 and all(a.get("confirmation", {}).get("text") for a in actions)
    assert {a["perform_action"] for a in actions} <= {"button.press", "lock.lock"}       # never unlock
    assert {a.get("action") for a in triggers(cfg)} <= {"none", "more-info", "perform-action", "navigate", "toggle"}
    for k in cards(cfg):
        if k["type"] != "tile":
            continue
        domain = k["entity"].split(".")[0]
        if k["entity"] in local:                            # preset: sends nothing to the car
            assert domain in {"number", "select", "switch"}
            continue
        assert "toggle" not in {a.get("action") for a in triggers(k)}, k
        if domain in SWITCHABLE:                            # the icon never switches without a dialog
            assert k.get("icon_tap_action", {}).get("action") in ("none", "perform-action"), k
            assert k.get("tap_action", {}).get("action") in ("none", "more-info", "perform-action"), k
        else:
            assert domain in {"sensor", "binary_sensor", "device_tracker"}, k
    lock = next(k for k in cards(cfg) if k.get("name") == "Lock state")
    assert all(lock[a]["action"] == "none" for a in ("tap_action", "icon_tap_action", "hold_action", "double_tap_action"))


def test_blocked_without_button_approval_with_hint(page):
    texts = confirmations(generate(page, data(push=True)))
    assert "button.t03_open_trunk" not in texts and "button.t03_locate_vehicle" not in texts
    assert "approve it on your phone" in texts["button.t03_open_windows"]
    assert "approve" not in texts["button.t03_close_windows"]
    assert texts["lock.t03_lock"].startswith("Lock")
    assert "approve" not in confirmations(generate(page, data(push=False)))["button.t03_open_windows"]
    assert "Freigabe aufs Telefon" in confirmations(generate(page, data(), "de"))["button.t03_open_windows"]


def test_buttons_in_their_sections(page):
    cfg = generate(page, data())
    sections = {s["cards"][0]["heading"]: {k.get("entity") for k in s["cards"]}
                for v in cfg["views"][:4] for s in v["sections"] if s["cards"][0]["type"] == "heading"}
    assert "button.t03_quick_heat" in sections["Commands to the car"]
    assert "button.t03_start_charging" in sections["Charging"]
    assert {"button.t03_close_windows", "lock.t03_lock"} <= sections["Controls"]


def test_without_climate_and_in_german(page):
    cfg = generate(page, data(), "de")
    c10 = [v for v in cfg["views"] if v["path"].startswith("vehicle-2")]
    assert not any(k.get("entity", "").startswith(("climate.", "number.", "select.")) for k in cards({"views": c10}))
    headings = [s["cards"][0]["heading"] for s in view(cfg, "vehicle-1-status")["sections"][1:]]
    assert "Bedienen" in headings and "Laden" in headings
    assert cfg["views"][0]["title"].endswith("Bordcomputer")


def test_offered_in_dialog(page):
    entry = page.evaluate('() => window.customStrategies.filter(s => s.type === "leapmotor-gateway")')
    assert len(entry) == 1 and entry[0]["strategyType"] == "dashboard" and entry[0]["name"]


def test_climate_not_twice(page):
    cl = view(generate(page, data()), "vehicle-1-climate")
    assert [k["entity"] for k in cards({"views": [cl]}) if k.get("name") in ("Climate", "climate")] == ["climate.t03_climate"]


def test_without_vehicle_and_without_integration(page):
    assert "No vehicle has been set up" in generate(page, {"vehicles": [], "commands": {}})["views"][0]["cards"][0]["content"]
    assert "does not respond" in generate(page, {}, error=True)["views"][0]["cards"][0]["content"]
