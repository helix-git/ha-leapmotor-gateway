"""Climate preset: temperature and fan speed. Changing them sends nothing to the car."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature

from .entity import PresetEntity, add_vehicles

NUMBERS = [("temperature", 18, 32, UnitOfTemperature.CELSIUS, NumberMode.BOX, "mdi:thermometer"),
           ("fan_speed", 1, 7, None, NumberMode.SLIDER, "mdi:fan")]


async def async_setup_entry(hass, entry, async_add_entities):
    add_vehicles(entry, async_add_entities, lambda c, vin: [GatewayPresetNumber(c, vin, *n) for n in NUMBERS])


class GatewayPresetNumber(PresetEntity, NumberEntity):
    def __init__(self, coordinator, vin, field, minimum, maximum, unit, mode, icon) -> None:
        super().__init__(coordinator, vin, f"preset_{field}")
        self._field = field
        self._attr_native_min_value, self._attr_native_max_value, self._attr_native_step = minimum, maximum, 1
        self._attr_native_unit_of_measurement, self._attr_mode, self._attr_icon = unit, mode, icon

    @property
    def native_value(self):
        return self.preset[self._field]

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_preset(self._vin, **{self._field: int(value)})

    def _from_state(self, state: str):
        try:
            value = int(float(state))
        except ValueError:
            return None
        return value if self._attr_native_min_value <= value <= self._attr_native_max_value else None
