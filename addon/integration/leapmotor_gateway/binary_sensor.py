"""Doors, charging cables, ready to drive."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass as K, BinarySensorEntity

from .entity import GatewayEntity, add_vehicles

BINARY = [("charging", K.BATTERY_CHARGING), ("ac_cable", K.PLUG), ("dc_cable", K.PLUG), ("ready", K.RUNNING),
          ("climate", None), ("driver_door", K.DOOR), ("passenger_door", K.DOOR), ("rear_left_door", K.DOOR),
          ("rear_right_door", K.DOOR), ("trunk", K.DOOR)]


async def async_setup_entry(hass, entry, async_add_entities):
    add_vehicles(entry, async_add_entities, lambda c, vin: [GatewayBinarySensor(c, vin, *b) for b in BINARY])


class GatewayBinarySensor(GatewayEntity, BinarySensorEntity):
    def __init__(self, coordinator, vin, key, device_class) -> None:
        super().__init__(coordinator, vin, key)
        self._attr_device_class = device_class

    @property
    def is_on(self):
        value = self.values.get(self._key)
        return None if value is None else bool(value)
