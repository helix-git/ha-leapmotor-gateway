"""Vehicle image, composed by the app to match doors, windows and plug."""
from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.util import dt as dt_util

from .api import GatewayError
from .entity import GatewayEntity, add_vehicles


async def async_setup_entry(hass, entry, async_add_entities):
    add_vehicles(entry, async_add_entities, lambda c, vin: [GatewayImage(hass, c, vin)])


class GatewayImage(GatewayEntity, ImageEntity):
    _attr_content_type = "image/png"

    def __init__(self, hass, coordinator, vin) -> None:
        GatewayEntity.__init__(self, coordinator, vin, "image")
        ImageEntity.__init__(self, hass)
        self._hash = None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and bool(self.vehicle.get("image_hash"))

    def _handle_coordinator_update(self) -> None:
        h = self.vehicle.get("image_hash")
        if h and h != self._hash:
            self._hash = h
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        try:
            return await self.coordinator.client.image(self._vin)
        except GatewayError:
            return None
