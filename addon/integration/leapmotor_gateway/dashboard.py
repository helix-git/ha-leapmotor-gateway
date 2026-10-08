"""Dashboard strategy of the integration.

The integration serves a JavaScript file that the frontend loads on every page load
(frontend/leapmotor-gateway-strategy.js). A dashboard with

    strategy:
      type: custom:leapmotor-gateway

builds one view per vehicle from built-in cards. Which entity has which role comes from the websocket
command leapmotor_gateway/dashboard, taken from the entity registry by the unique_id {VIN}_{key}. This
fits every model, several cars and renamed entities. The VIN is not sent to the frontend.
"""
from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration

from .const import DOMAIN

FILES = "/leapmotor_gateway/frontend"        # strategy and the top view for the tyre pressure image
JS_PATH = f"{FILES}/leapmotor-gateway-strategy.js"


async def async_setup(hass: HomeAssistant) -> None:
    """Once per Home Assistant run: serve the files, register with the frontend, websocket command."""
    version = (await async_get_integration(hass, DOMAIN)).version
    # no long term cache: the browser asks whether the file has changed
    await hass.http.async_register_static_paths([StaticPathConfig(FILES, str(Path(__file__).parent / "frontend"), False)])
    if "frontend" in hass.config.components:
        from homeassistant.components.frontend import add_extra_js_url

        add_extra_js_url(hass, f"{JS_PATH}?v={version}")          # the version forces the new file after an update
    websocket_api.async_register_command(hass, _ws_dashboard)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/dashboard"})
@callback
def _ws_dashboard(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Vehicles with their entities by role, plus the commands with their level."""
    reg = er.async_get(hass)
    vehicles: list[dict] = []
    commands: dict = {}
    push = True
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        c = entry.runtime_data
        gw = c.gateway()
        levels = gw.get("levels") or {}
        commands.update({n: {**cmd, "level": cmd.get("level") or levels.get(n)}
                         for n, cmd in (gw.get("commands") or {}).items()})
        push = gw.get("approval_push", True)
        entries = er.async_entries_for_config_entry(reg, entry.entry_id)
        for vin, v in ((c.data or {}).get("vehicles") or {}).items():
            roles = {e.unique_id[len(vin) + 1:]: {"entity_id": e.entity_id, "name": e.name or e.original_name}
                     for e in entries if e.unique_id.startswith(f"{vin}_") and e.disabled_by is None}
            vehicles.append({"name": v.get("name") or f"Leapmotor {vin[-6:]}", "plate": v.get("plate"),
                             "model": v.get("model"), "entities": roles})
    connection.send_result(msg["id"], {"vehicles": vehicles, "commands": commands, "approval_push": push})
