"""Buttons for the allowlisted commands. The list comes from the app."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .const import COMMANDS
from .entity import GatewayEntity, add_vehicles


def _buttons(c, vin):
    commands = c.gateway().get("commands") or {}
    buttons = [GatewayButton(c, vin, name, cmd.get("title"), cmd.get("icon")) for name, cmd in commands.items()
               if cmd.get("button")]
    buttons.append(GatewayButton(c, vin, "refresh", None, "mdi:refresh"))
    buttons.append(GatewayClimateStart(c, vin))
    return buttons


async def async_setup_entry(hass, entry, async_add_entities):
    add_vehicles(entry, async_add_entities, _buttons)


class GatewayButton(GatewayEntity, ButtonEntity):
    def __init__(self, coordinator, vin, action, title, icon) -> None:
        # Known commands are translated. A command added in a newer app uses its title from the app.
        super().__init__(coordinator, vin, f"button_{action}", None if action in COMMANDS else (title or action))
        self._action, self._attr_icon = action, icon

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_press(self) -> None:
        if self._action == "climate_off":     # with the preset temperature, see climate preset
            await self._command("climate_off", {"temperature": int(self.coordinator.preset(self._vin)["temperature"])})
            return
        await self._command(self._action)


class GatewayClimateStart(GatewayEntity, ButtonEntity):
    """Sends the climate preset (mode, temperature, fan speed, recirculation) as "climate_on"."""

    _attr_icon = "mdi:play-circle"

    def __init__(self, coordinator, vin) -> None:
        super().__init__(coordinator, vin, "start_climate")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_press(self) -> None:
        await self._command("climate_on", self.coordinator.climate_params(self._vin))
