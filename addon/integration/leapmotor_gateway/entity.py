"""Common base: one device per vehicle (VIN), values from the coordinator."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def add_vehicles(entry, async_add_entities, factory: Callable[[Any, str], list]) -> None:
    """Create entities for all vehicles, and for every vehicle added to the app later, without a restart."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new():
        vins = [v for v in ((coordinator.data or {}).get("vehicles") or {}) if v not in known]
        if vins:
            known.update(vins)
            async_add_entities([e for v in vins for e in factory(coordinator, v)])

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class GatewayEntity(CoordinatorEntity):
    """The key is part of the unique_id and, unless a fixed name is given, the translation key."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, vin: str, key: str, name: str | None = None) -> None:
        super().__init__(coordinator)
        v = coordinator.vehicle(vin)
        self._vin, self._key = vin, key
        if name is None:
            self._attr_translation_key = key
        else:
            self._attr_name = name
        self._attr_unique_id = f"{vin}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, vin)}, name=v.get("name") or f"Leapmotor {vin[-6:]}",
                                            manufacturer="Leapmotor", model=v.get("model") or None, serial_number=vin)

    @property
    def vehicle(self) -> dict[str, Any]:
        return self.coordinator.vehicle(self._vin)

    @property
    def values(self) -> dict[str, Any]:
        return self.vehicle.get("values") or {}

    @property
    def extras(self) -> dict[str, Any]:
        return self.vehicle.get("extras") or {}

    @property
    def available(self) -> bool:
        return super().available and bool(self.values)

    async def _command(self, action: str, params: dict | None = None) -> dict:
        return await self.coordinator.command(action, params, self._context, self._vin)


class PresetEntity(GatewayEntity, RestoreEntity):
    """Climate preset: chosen in Home Assistant only, sends nothing to the car. "Start climate" takes the
    values along. Survives restarts (last state)."""

    _attr_entity_category = EntityCategory.CONFIG
    _field: str

    @property
    def available(self) -> bool:
        return True

    @property
    def preset(self) -> dict:
        return self.coordinator.preset(self._vin)

    def _from_state(self, state: str):
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, f"{DOMAIN}_preset_{self._vin}", self.async_write_ha_state))
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unknown", "unavailable"):
            value = self._from_state(last.state)
            if value is not None:
                self.coordinator.set_preset(self._vin, **{self._field: value})
