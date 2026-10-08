"""Climate preset: mode (heating, cooling, ventilation). Changing it sends nothing to the car."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .entity import PresetEntity, add_vehicles


async def async_setup_entry(hass, entry, async_add_entities):
    add_vehicles(entry, async_add_entities, lambda c, vin: [GatewayClimateMode(c, vin)])


class GatewayClimateMode(PresetEntity, SelectEntity):
    _attr_options = ["hot", "cold", "wind"]          # as the app expects them, shown through the translation
    _attr_icon = "mdi:air-conditioner"
    _field = "mode"

    def __init__(self, coordinator, vin) -> None:
        super().__init__(coordinator, vin, "preset_mode")

    @property
    def current_option(self) -> str | None:
        return self.preset["mode"]

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_preset(self._vin, mode=option)

    def _from_state(self, state: str):
        return state if state in self._attr_options else None
