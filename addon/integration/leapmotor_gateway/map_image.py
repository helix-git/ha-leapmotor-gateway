"""Map marker of the location: the vehicle image from the app, cropped to the car.

The map shows the tracker's `entity_picture` at its location, otherwise the initials of the name.
The vehicle image has wide transparent margins, so a round marker would only show its middle. It is
therefore cropped to the car and set into a square. Every installation gets its own car in model and
colour, without a file under /local and without customize.

It is served by its own view. The tracker carries a path signed by Home Assistant (like media URLs:
valid 24 h, invalid after a restart), so the browser needs no token. Without a valid signature the
view answers 403 like the Home Assistant image proxy. A 401 would count as a failed login for the IP
ban, and an old address after a restart is no attack.
"""
from __future__ import annotations

import hashlib
import io
import logging

from aiohttp import hdrs, web

from homeassistant.components.http import KEY_AUTHENTICATED, HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PATH = "/api/leapmotor_gateway/map_image/{key}"
EDGE = 256       # px. The marker is 48 px, this stays sharp on dense displays.
MARGIN = 1.12    # some air so the car does not touch the circle
OPAQUE = 40      # alpha threshold for cropping: the soft ground shadow (alpha 1 to 40) is not part of the car


def key_for(vin: str) -> str:
    """Short key for the address, so the VIN appears in no URL or log."""
    return hashlib.sha256(vin.encode()).hexdigest()[:16]


def crop(png: bytes) -> bytes:
    """Crop to the visible pixels and centre in a square with margin, EDGE px. Without Pillow or with a
    broken image the input comes back unchanged: the whole image is better than none. Runs in the executor."""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(png)).convert("RGBA")
        box = image.getchannel("A").point(lambda a: 255 if a > OPAQUE else 0).getbbox()
        car = image.crop(box or (0, 0, *image.size))
        side = max(1, int(max(car.size) * MARGIN))
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(car, ((side - car.width) // 2, (side - car.height) // 2), car)
        out = io.BytesIO()
        square.resize((EDGE, EDGE), Image.LANCZOS).save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Map image not cropped: %s", err)
        return png


class MapImageView(HomeAssistantView):
    """The cropped image, only for logged in users or with a signed path."""

    url = PATH
    name = f"api:{DOMAIN}:map_image"
    requires_auth = False        # checked here, see module docstring

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, key: str) -> web.Response:
        if not request[KEY_AUTHENTICATED]:
            if hdrs.AUTHORIZATION in request.headers:
                raise web.HTTPUnauthorized       # wrong token: the IP ban should see this
            raise web.HTTPForbidden
        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            coordinator = entry.runtime_data
            for vin in (coordinator.data or {}).get("vehicles") or {}:
                if key_for(vin) == key and (png := await coordinator.map_image(vin)):
                    return web.Response(body=png, content_type="image/png",
                                        headers={hdrs.CACHE_CONTROL: "private, max-age=86400"})
        raise web.HTTPNotFound
