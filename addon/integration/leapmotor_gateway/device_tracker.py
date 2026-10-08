"""Location of the vehicle, shown on the map with the image of the own car."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.components.http.auth import async_sign_path
from homeassistant.const import ATTR_ENTITY_PICTURE
from homeassistant.util import dt as dt_util

from .entity import GatewayEntity, add_vehicles
from .map_image import PATH, key_for

VALID = timedelta(hours=24)
RESIGN = timedelta(hours=12)   # half the validity, so the path in the state still holds for 12 h


async def async_setup_entry(hass, entry, async_add_entities):
    add_vehicles(entry, async_add_entities, lambda c, vin: [GatewayTracker(c, vin, "location")])


class GatewayTracker(GatewayEntity, TrackerEntity):
    _attr_source_type = SourceType.GPS
    # The signed path expires and changes twice a day. It does not belong in the database.
    _unrecorded_attributes = frozenset({ATTR_ENTITY_PICTURE})
    _picture: tuple[str, str, object] | None = None

    @property
    def latitude(self):
        return self.values.get("latitude")

    @property
    def longitude(self):
        return self.values.get("longitude")

    @property
    def location_accuracy(self) -> int:
        return 30

    @property
    def extra_state_attributes(self) -> dict | None:
        plate = self.vehicle.get("plate")
        return {"plate": plate} if plate else None

    @property
    def entity_picture(self) -> str | None:
        """Signed path to the map image. Renewed only for a new image (doors, plug) or after 12 h,
        otherwise every poll every 15 s would write a new state."""
        h = self.vehicle.get("image_hash")
        if not h:
            return None
        now = dt_util.utcnow()
        if not self._picture or self._picture[0] != h or now - self._picture[2] >= RESIGN:
            path = PATH.format(key=key_for(self._vin)) + f"?v={h}"
            self._picture = (h, async_sign_path(self.hass, path, VALID, use_content_user=True), now)
        return self._picture[1]
