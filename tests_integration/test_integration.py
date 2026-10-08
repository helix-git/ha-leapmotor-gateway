import io
import json
import pathlib
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leapmotor_gateway.const import DOMAIN

from .conftest import VIN, VIN2, vehicle

DATA = {"host": "local-leapmotor-gateway", "port": 8789, "token": "secret"}
TRACKER = "device_tracker.leapmotor_t03_location"
LOCK = "lock.leapmotor_t03_lock"
CLIMATE = "climate.leapmotor_t03_climate"


async def set_up(hass, entry=None):
    entry = entry or MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN)
    if entry.entry_id not in {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def refresh(hass):
    await hass.config_entries.async_entries(DOMAIN)[0].runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_supervisor_discovery(hass, gateway):
    info = HassioServiceInfo(config={**DATA}, name="Leapmotor Gateway", slug="local_leapmotor_gateway", uuid="x")
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=info)
    assert r["type"] == "form" and r["step_id"] == "hassio_confirm"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["type"] == "create_entry" and r["data"]["token"] == "secret"
    # a second discovery (for example a new token) creates no second entry
    info2 = HassioServiceInfo(config={**DATA, "token": "new"}, name="Leapmotor Gateway", slug="local_leapmotor_gateway", uuid="x")
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=info2)
    assert r["type"] == "abort"
    assert hass.config_entries.async_entries(DOMAIN)[0].data["token"] == "new"


async def test_entities(hass, gateway):
    await set_up(hass)
    assert hass.states.get("sensor.leapmotor_t03_battery").state == "37"
    assert hass.states.get("sensor.leapmotor_t03_available_energy").state == "14.01"
    assert hass.states.get("sensor.leapmotor_t03_tyre_pressure_rear_left").state == "2.58"
    assert hass.states.get("sensor.leapmotor_t03_driving_energy_last_week").state == "92.2"
    assert hass.states.get(LOCK).state == "locked"
    assert hass.states.get("sensor.leapmotor_t03_trip_ends_after").state == "10"      # setting from the app
    assert hass.states.get("binary_sensor.leapmotor_t03_driver_door").state == "off"
    assert hass.states.get(CLIMATE).state == "off"
    assert hass.states.get("button.leapmotor_t03_quick_heat") is not None
    assert hass.states.get("button.leapmotor_t03_something_new") is not None          # unknown command: title from the app
    assert hass.states.get("button.leapmotor_t03_unlock") is None                      # only through the lock
    t = hass.states.get(TRACKER)
    assert t.attributes["latitude"] == 52.52
    assert hass.states.get("sensor.leapmotor_t03_licence_plate").state == "B-LM 123E"
    assert t.attributes["plate"] == "B-LM 123E"
    assert hass.states.get("sensor.leapmotor_t03_last_command").state == "climate_off: dry run, not sent"


async def test_unlock_with_user(hass, gateway, hass_admin_user):
    await set_up(hass)
    gateway.command.return_value = {"result": "waiting for approval"}
    await hass.services.async_call("lock", "unlock", {"entity_id": LOCK}, blocking=True,
                                   context=Context(user_id=hass_admin_user.id))
    action, params, user_id, name, vin = gateway.command.call_args.args
    assert (action, user_id, name, vin) == ("unlock", hass_admin_user.id, hass_admin_user.name, VIN)


async def test_refusal_raises(hass, gateway):
    await set_up(hass)
    gateway.command.return_value = {"result": "no permission (role limited)"}
    with pytest.raises(ServiceValidationError, match="no permission"):
        await hass.services.async_call("lock", "unlock", {"entity_id": LOCK}, blocking=True)
    assert gateway.command.call_args.args[2] is None                              # automation, no user
    gateway.command.return_value = {"result": "something", "ok": False}           # an explicit ok field wins
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("lock", "lock", {"entity_id": LOCK}, blocking=True)


async def test_climate(hass, gateway):
    await set_up(hass)
    await hass.services.async_call("climate", "set_temperature", {"entity_id": CLIMATE, "temperature": 21}, blocking=True)
    gateway.command.assert_not_called()                                          # off: only remembered
    await hass.services.async_call("climate", "set_fan_mode", {"entity_id": CLIMATE, "fan_mode": "2"}, blocking=True)
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": CLIMATE, "hvac_mode": "heat"}, blocking=True)
    action, params = gateway.command.call_args.args[:2]
    assert action == "climate_on" and params == {"mode": "hot", "temperature": 21, "fan_speed": 2, "recirculation": False}
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": CLIMATE, "hvac_mode": "off"}, blocking=True)
    assert gateway.command.call_args.args[0] == "climate_off"
    gateway.data["vehicles"][VIN]["values"].update(climate=True, climate_mode="heat")
    await refresh(hass)
    assert hass.states.get(CLIMATE).state == "heat"


async def test_command_service(hass, gateway, hass_admin_user):
    await set_up(hass)
    r = await hass.services.async_call(DOMAIN, "command", {"action": "climate_on", "params": {"mode": "hot", "temperature": 22}},
                                       blocking=True, return_response=True, context=Context(user_id=hass_admin_user.id))
    assert r["result"].startswith("dry run")
    assert gateway.command.call_args.args[:3] == ("climate_on", {"mode": "hot", "temperature": 22}, hass_admin_user.id)
    assert gateway.command.call_args.args[4] is None                             # no vin: the app picks it if unambiguous
    assert not hass.services.has_service(DOMAIN, "befehl")


async def test_gateway_gone(hass, gateway):
    """A short outage (app update) keeps the values. Only after 3 min the entities become unavailable."""
    await set_up(hass)
    from custom_components.leapmotor_gateway.api import GatewayError
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    gateway.vehicles.side_effect = GatewayError("gone")
    await refresh(hass)
    assert hass.states.get("sensor.leapmotor_t03_battery").state == "37"
    c._last_ok -= 181
    await refresh(hass)
    assert hass.states.get("sensor.leapmotor_t03_battery").state == "unavailable"
    before = c._last_ok
    gateway.vehicles.side_effect = None                                          # back again: grace period restarts
    await refresh(hass)
    assert hass.states.get("sensor.leapmotor_t03_battery").state == "37" and c._last_ok > before + 100


async def test_image(hass, gateway, hass_client):
    await set_up(hass)
    st = hass.states.get("image.leapmotor_t03_vehicle_image")
    assert st is not None and st.state != "unavailable"
    client = await hass_client()
    r = await client.get(st.attributes["entity_picture"])
    assert r.status == 200 and (await r.read()).startswith(b"\x89PNG")


async def test_second_vehicle_without_restart(hass, gateway, hass_admin_user):
    await set_up(hass)
    assert hass.states.get("sensor.cupra_battery") is None
    gateway.data["vehicles"][VIN2] = vehicle("Cupra", VIN2, battery=80)
    await refresh(hass)
    assert hass.states.get("sensor.cupra_battery").state == "80"
    assert hass.states.get("sensor.leapmotor_t03_battery").state == "37"
    await hass.services.async_call("lock", "lock", {"entity_id": "lock.cupra_lock"}, blocking=True,
                                   context=Context(user_id=hass_admin_user.id))
    assert gateway.command.call_args.args[0] == "lock" and gateway.command.call_args.args[4] == VIN2


async def test_discovery_updates_existing_entry(hass, gateway):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=VIN)               # entry of an early version
    entry.add_to_hass(hass)
    info = HassioServiceInfo(config={**DATA, "token": "new"}, name="Leapmotor Gateway", slug="local_leapmotor_gateway", uuid="x")
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=info)
    assert r["type"] == "abort" and entry.data["token"] == "new"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    # The update reloads the entry in the background. Wait for it and unload, so no timer lingers.
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_german_unique_ids_are_migrated(hass, gateway):
    """Registry entries of versions before 0.7.0 keep their entity_id and get the English key."""
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    old = {("sensor", "akku"): "sensor.garage_t03_akku", ("lock", "schloss"): "lock.garage_t03_verriegelung",
           ("button", "knopf_schnell_heizen"): "button.garage_t03_schnell_heizen",
           ("button", "knopf_aktualisieren"): "button.garage_t03_daten_abfragen",
           ("climate", "klima_steuerung"): "climate.garage_t03_klima",
           ("binary_sensor", "klima"): "binary_sensor.garage_t03_klima",
           ("number", "vorwahl_temperatur"): "number.garage_t03_wunschtemperatur",
           ("sensor", "fahrt_km"): "sensor.garage_t03_fahrt_strecke"}
    for (domain, key), entity_id in old.items():
        reg.async_get_or_create(domain, DOMAIN, f"{VIN}_{key}", suggested_object_id=entity_id.split(".")[1],
                                config_entry=entry)
    # both old and new exist: the old one is left alone
    reg.async_get_or_create("sensor", DOMAIN, f"{VIN}_reichweite", suggested_object_id="old_range", config_entry=entry)
    reg.async_get_or_create("sensor", DOMAIN, f"{VIN}_range", suggested_object_id="new_range", config_entry=entry)
    await set_up(hass, entry)
    expected = {"sensor.garage_t03_akku": "battery", "lock.garage_t03_verriegelung": "lock",
                "button.garage_t03_schnell_heizen": "button_quick_heat", "button.garage_t03_daten_abfragen": "button_refresh",
                "climate.garage_t03_klima": "climate_control", "binary_sensor.garage_t03_klima": "climate",
                "number.garage_t03_wunschtemperatur": "preset_temperature", "sensor.garage_t03_fahrt_strecke": "trip_distance"}
    for entity_id, key in expected.items():
        assert reg.async_get(entity_id).unique_id == f"{VIN}_{key}", entity_id
    assert hass.states.get("sensor.garage_t03_akku").state == "37"
    assert hass.states.get("lock.garage_t03_verriegelung").state == "locked"
    assert reg.async_get("sensor.old_range").unique_id == f"{VIN}_reichweite"
    assert hass.states.get("sensor.new_range").state == "92"
    assert hass.states.get("sensor.leapmotor_t03_battery") is None               # no duplicate


def car_png():
    """Like the image from the app: a car (100 x 50 px) in the middle of a wide transparent margin."""
    from PIL import Image
    image = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    image.paste((0, 0, 0, 12), (20, 120, 380, 150))               # soft ground shadow, wider than the car
    image.paste((0, 160, 160, 255), (150, 80, 250, 130))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


async def test_map_image(hass, gateway, hass_client, hass_client_no_auth):
    from PIL import Image
    from custom_components.leapmotor_gateway.map_image import crop
    gateway.image.return_value = car_png()
    await set_up(hass)
    path = hass.states.get(TRACKER).attributes["entity_picture"]
    assert path.startswith("/api/leapmotor_gateway/map_image/") and "authSig=" in path and VIN not in path
    client = await hass_client_no_auth()
    r = await client.get(path)                                                   # as the browser loads it
    assert r.status == 200 and r.content_type == "image/png"
    image = Image.open(io.BytesIO(await r.read()))
    left, top, right, bottom = image.getchannel("A").point(lambda a: 255 * (a > 128)).getbbox()   # without soft edges
    assert image.size == (256, 256)
    assert 224 <= right - left <= 234 and 110 <= bottom - top <= 120            # up to the margin, aspect ratio kept
    assert abs(left + right - 256) <= 3 and abs(top + bottom - 256) <= 3        # centred
    # without or with a broken signature 403, not 401: no failed login for the IP ban
    bare = path.split("?")[0]
    assert (await client.get(bare)).status == 403
    assert (await client.get(path.replace("v=abc", "v=xyz"))).status == 403
    assert (await client.get(bare, headers={"Authorization": "Bearer wrong"})).status == 401
    logged_in = await hass_client()
    assert (await logged_in.get(bare)).status == 200
    assert (await logged_in.get("/api/leapmotor_gateway/map_image/0000000000000000")).status == 404
    assert gateway.image.await_count == 1                                        # cropped once, then cached
    assert crop(b"broken") == b"broken"                                          # the whole image is better than none


async def test_map_image_path_changes_only_with_new_image(hass, gateway, freezer):
    await set_up(hass)
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data

    async def poll():
        await c.async_refresh()
        await hass.async_block_till_done()
        return hass.states.get(TRACKER).attributes.get("entity_picture")

    before = hass.states.get(TRACKER).attributes["entity_picture"]
    freezer.tick(timedelta(hours=1))
    assert await poll() == before                                                # not signed again on every poll
    gateway.data["vehicles"][VIN]["image_hash"] = "def"                           # for example a door opened
    new = await poll()
    assert "v=def" in new and new != before
    freezer.tick(timedelta(hours=12))
    assert await poll() not in (new, None)                                       # signed again after 12 h
    gateway.data["vehicles"][VIN]["image_hash"] = None
    assert await poll() is None                                                  # no image yet: initials


async def test_dashboard_strategy(hass, gateway, hass_ws_client, hass_client_no_auth):
    """The strategy gets the entities by role, without VIN. The JS file is served openly."""
    await set_up(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "leapmotor_gateway/dashboard"})
    r = await ws.receive_json()
    assert r["success"], r
    d = r["result"]
    assert VIN not in json.dumps(d)
    (v,) = d["vehicles"]
    assert v["name"] == "Leapmotor T03" and v["plate"] == "B-LM 123E" and v["model"] == "T03"
    e = v["entities"]
    assert e["battery"] == {"entity_id": "sensor.leapmotor_t03_battery", "name": "Battery"}
    assert e["lock"]["entity_id"] == LOCK
    assert e["climate_control"]["entity_id"] == CLIMATE
    assert e["button_quick_heat"]["entity_id"] == "button.leapmotor_t03_quick_heat"
    assert e["button_refresh"]["entity_id"] == "button.leapmotor_t03_refresh_data"
    assert e["image"]["entity_id"] == "image.leapmotor_t03_vehicle_image"
    assert e["location"]["entity_id"] == TRACKER
    assert d["commands"]["unlock"]["level"] == "blocked" and d["commands"]["quick_heat"]["title"] == "Quick heat"
    assert d["approval_push"] is True
    assert {"tyre_pressure", "state", "trips", "charges", "preset_temperature", "preset_fan_speed", "preset_mode",
            "preset_recirculation", "start_climate"} <= set(e)
    client = await hass_client_no_auth()
    js = await client.get("/leapmotor_gateway/frontend/leapmotor-gateway-strategy.js")
    assert js.status == 200 and "ll-strategy-dashboard-leapmotor-gateway" in await js.text()
    svg = await client.get("/leapmotor_gateway/frontend/top-view.svg")
    assert svg.status == 200 and "<svg" in await svg.text()


async def test_dashboard_registered_with_frontend(hass, gateway):
    """With the frontend running it loads the strategy. The version in the path forces the new file after updates."""
    hass.config.components.add("frontend")
    with patch("homeassistant.components.frontend.add_extra_js_url") as register:
        await set_up(hass)
    (call,) = register.call_args_list
    version = json.loads((pathlib.Path(__file__).parents[1] / "addon/integration/leapmotor_gateway/manifest.json").read_text())["version"]
    assert call.args[1] == f"/leapmotor_gateway/frontend/leapmotor-gateway-strategy.js?v={version}"


async def test_trip_computer_and_state(hass, gateway):
    from homeassistant.setup import async_setup_component
    assert await async_setup_component(hass, "zone", {"zone": [{"name": "Charger Test", "latitude": 52.5001,
                                                                 "longitude": 13.4001, "radius": 80}]})
    await set_up(hass)
    st = lambda e: hass.states.get(f"sensor.leapmotor_t03_{e}")
    assert (st("trip_distance").state, st("trip_duration").state, st("trip_consumption").state) == ("8.0", "29", "18.5")
    assert st("trip_distance").attributes["ongoing"] is False
    assert (st("distance_since_charge").state, st("energy_since_charge").state) == ("57.0", "9.1")
    assert st("last_charge").state == "7.9" and st("last_charge").attributes["place"] == "Charger Test"
    assert st("charges").state == "2" and st("charges").attributes["items"][0]["soc_to"] == 99
    assert st("charges").attributes["items"][0]["place"] == "Charger Test"
    assert st("trips").state == "9" and len(st("trips").attributes["items"]) == 1
    assert st("state").state == "parked" and st("charge_state").state == "not_charging"
    assert st("gear").state in ("park", "unknown") and st("sunshade").attributes.get("unit_of_measurement") == "%"
    r = st("tyre_pressure")
    assert r.state == "2.58" and r.attributes["warning"] is False and r.attributes["rear_left"] == 2.58
    assert r.attributes["icon"] == "mdi:tire"
    values = gateway.data["vehicles"][VIN]["values"]
    values.update(ready=True, tyre_rl=2.2, charge_state="new_unknown")
    await refresh(hass)
    assert st("state").state == "driving" and st("charge_state").state == "unknown"   # unknown value: empty, no error
    assert st("tyre_pressure").attributes["warning"] is True and st("tyre_pressure").attributes["icon"] == "mdi:car-tire-alert"
    assert st("charge_end_forecast").state == "unknown" and st("energy_to_charge").state == "unknown"   # not charging
    values.update(ready=False, charging=True, charge_time_left=200, charging_power=6.44, battery=30, energy=11.42,
                  vehicle_time="2026-10-07T14:15:00+00:00")
    await refresh(hass)
    assert st("state").state == "charging"
    assert st("charge_end_forecast").state == "2026-10-07T17:35:00+00:00"         # charge time left from the car
    assert st("energy_to_charge").state == "21.5"                                 # 6.44 kW x 200 min, below "until full"
    values.update(charging=False, dc_cable=True)
    await refresh(hass)
    assert st("state").state == "plugged_in"


async def test_climate_preset_and_start(hass, gateway, hass_admin_user):
    await set_up(hass)
    ctx = Context(user_id=hass_admin_user.id)
    await hass.services.async_call("number", "set_value", {"entity_id": "number.leapmotor_t03_preset_temperature", "value": 20},
                                   blocking=True, context=ctx)
    await hass.services.async_call("number", "set_value", {"entity_id": "number.leapmotor_t03_preset_fan_speed", "value": 5},
                                   blocking=True, context=ctx)
    await hass.services.async_call("select", "select_option", {"entity_id": "select.leapmotor_t03_climate_mode", "option": "cold"},
                                   blocking=True, context=ctx)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.leapmotor_t03_recirculation"}, blocking=True, context=ctx)
    gateway.command.assert_not_called()                                          # the preset sends nothing to the car
    assert hass.states.get(CLIMATE).attributes["temperature"] == 20              # the climate entity shares the preset
    await hass.services.async_call("button", "press", {"entity_id": "button.leapmotor_t03_start_climate"}, blocking=True, context=ctx)
    assert gateway.command.call_args.args[:2] == ("climate_on", {"mode": "cold", "temperature": 20, "fan_speed": 5, "recirculation": True})
    await hass.services.async_call("button", "press", {"entity_id": "button.leapmotor_t03_climate_off"}, blocking=True, context=ctx)
    assert gateway.command.call_args.args[:2] == ("climate_off", {"temperature": 20})
    await hass.services.async_call("climate", "set_temperature", {"entity_id": CLIMATE, "temperature": 23},
                                   blocking=True, context=ctx)
    assert hass.states.get("number.leapmotor_t03_preset_temperature").state == "23"


async def test_preset_survives_restart(hass, gateway):
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import mock_restore_cache
    mock_restore_cache(hass, [State("number.leapmotor_t03_preset_temperature", "19"),
                              State("select.leapmotor_t03_climate_mode", "wind"), State("switch.leapmotor_t03_recirculation", "on")])
    await set_up(hass)
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert c.climate_params(VIN) == {"mode": "wind", "temperature": 19, "fan_speed": 3, "recirculation": True}
