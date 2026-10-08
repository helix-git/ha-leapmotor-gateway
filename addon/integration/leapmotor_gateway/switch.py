"""Climate preset: recirculation. Switching it sends nothing to the car."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .entity import PresetEntity, add_vehicles


async def async_setup_entry(hass, entry, async_add_entities):
    add_vehicles(entry, async_add_entities, lambda c, vin: [GatewayRecirculation(c, vin)])


class GatewayRecirculation(PresetEntity, SwitchEntity):
    _attr_icon = "mdi:car-arrow-left"
    _field = "recirculation"

    def __init__(self, coordinator, vin) -> None:
        super().__init__(coordinator, vin, "preset_recirculation")

    @property
    def is_on(self) -> bool:
        return bool(self.preset["recirculation"])

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.set_preset(self._vin, recirculation=True)

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.set_preset(self._vin, recirculation=False)

    def _from_state(self, state: str):
        return {"on": True, "off": False}.get(state)
