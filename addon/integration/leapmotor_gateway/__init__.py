"""Leapmotor Gateway: the Home Assistant side of the app.

The app alone holds accounts, PINs and the app certificate. This integration reads the state of all
vehicles locally from the app and sends commands together with the Home Assistant user who triggered
them and the VIN. The app decides on role, level and approval. Vehicles added in the app appear here
as new devices without a restart.
"""
from __future__ import annotations

from datetime import timedelta
import logging
import time

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GatewayClient, GatewayError
from .const import (CONF_HOST, CONF_PORT, CONF_TOKEN, DOMAIN, GRACE_S, LEGACY_KEYS, PLATFORMS, PRESET_DEFAULT,
                    SCAN_INTERVAL_S)
from . import dashboard
from .map_image import MapImageView, crop

_LOGGER = logging.getLogger(__name__)

SERVICE_SCHEMA = vol.Schema({vol.Required("action"): cv.string, vol.Optional("params", default={}): dict,
                             vol.Optional("vin"): cv.string})
# Results the app reports when a command did not go through. Used when the answer has no "ok" field.
ERROR_RESULTS = ("no permission", "blocked", "unknown", "invalid", "error", "repeated too fast", "hourly limit",
                 "approval not deliverable", "vehicle unknown", "rejected", "expired")


class GatewayCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: GatewayClient) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=SCAN_INTERVAL_S),
                         config_entry=entry)
        self.client = client
        self._map_images: dict[str, tuple[str, bytes]] = {}
        self._presets: dict[str, dict] = {}
        self._last_ok: float | None = None

    async def _async_update_data(self) -> dict:
        try:
            data = await self.client.vehicles()
        except GatewayError as err:
            # A short outage, for example while the app updates, keeps the last values instead of
            # making every entity unavailable for a moment.
            if self.data is not None and self._last_ok and time.monotonic() - self._last_ok < GRACE_S:
                _LOGGER.debug("Gateway briefly unreachable, keeping last values: %s", err)
                return self.data
            raise UpdateFailed(str(err)) from err
        self._last_ok = time.monotonic()
        return data

    def vehicle(self, vin: str) -> dict:
        return ((self.data or {}).get("vehicles") or {}).get(vin) or {}

    def gateway(self) -> dict:
        return (self.data or {}).get("gateway") or {}

    def preset(self, vin: str) -> dict:
        """Climate preset per vehicle. It is only chosen in Home Assistant. "Start climate" sends it."""
        return self._presets.setdefault(vin, dict(PRESET_DEFAULT))

    @callback
    def set_preset(self, vin: str, **values) -> None:
        self.preset(vin).update(values)
        async_dispatcher_send(self.hass, f"{DOMAIN}_preset_{vin}")

    def climate_params(self, vin: str) -> dict:
        p = self.preset(vin)
        return {"mode": p["mode"], "temperature": int(p["temperature"]), "fan_speed": int(p["fan_speed"]),
                "recirculation": bool(p["recirculation"])}

    async def map_image(self, vin: str) -> bytes | None:
        """Vehicle image for the map marker, cropped once per image version."""
        h = self.vehicle(vin).get("image_hash")
        if not h:
            return None
        if (cached := self._map_images.get(vin)) and cached[0] == h:
            return cached[1]
        try:
            png = await self.client.image(vin)
        except GatewayError:
            return None
        small = await self.hass.async_add_executor_job(crop, png)
        self._map_images[vin] = (h, small)
        return small

    async def command(self, action: str, params: dict | None, context, vin: str | None) -> dict:
        """Send with the triggering user. Without a user (automation) the app applies the role "limited"."""
        user_id = getattr(context, "user_id", None)
        name = ""
        if user_id:
            user = await self.hass.auth.async_get_user(user_id)
            name = user.name if user else ""
        try:
            result = await self.client.command(action, params, user_id, name, vin)
        except GatewayError as err:
            raise HomeAssistantError(f"Gateway: {err}") from err
        text = str(result.get("result", ""))
        await self.async_request_refresh()
        failed = result.get("ok") is False if "ok" in result else text.lower().startswith(ERROR_RESULTS)
        if failed:
            # Refused by the gateway (role, level, limit): a user error, not a fault of Home Assistant.
            raise ServiceValidationError(f"{action}: {text}")
        return result


async def _async_migrate_unique_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rename German entity keys of versions before 0.7.0. Entity ids and history stay."""
    reg = er.async_get(hass)

    @callback
    def migrate(entity: er.RegistryEntry) -> dict | None:
        vin, sep, key = entity.unique_id.partition("_")
        new_key = LEGACY_KEYS.get(key) if sep else None
        if new_key is None:
            return None
        new_id = f"{vin}_{new_key}"
        if reg.async_get_entity_id(entity.domain, DOMAIN, new_id):
            _LOGGER.warning("Not migrating %s, %s already exists", entity.entity_id, new_id)
            return None
        return {"new_unique_id": new_id}

    await er.async_migrate_entries(hass, entry.entry_id, migrate)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _async_migrate_unique_ids(hass, entry)
    client = GatewayClient(async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT],
                           entry.data[CONF_TOKEN])
    coordinator = GatewayCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    if not hass.data.get(f"{DOMAIN}_registered"):   # views, static paths and commands cannot be removed: once per run
        hass.data[f"{DOMAIN}_registered"] = True
        hass.http.register_view(MapImageView(hass))
        await dashboard.async_setup(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def command_service(call: ServiceCall):
        return await coordinator.command(call.data["action"], call.data["params"], call.context, call.data.get("vin"))

    if not hass.services.has_service(DOMAIN, "command"):
        hass.services.async_register(DOMAIN, "command", command_service, schema=SERVICE_SCHEMA,
                                     supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok and not hass.config_entries.async_loaded_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, "command")
    return ok
