"""Climate as a Home Assistant entity: mode, temperature 18 to 32 °C, fan speed 1 to 7.

A change while the climate runs sends a new command right away. While it is off, the choice goes into
the climate preset, shared with the preset temperature, fan speed, mode and recirculation entities.
"""
from __future__ import annotations

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature as F, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN
from .entity import GatewayEntity, add_vehicles

MODES = {HVACMode.COOL: "cold", HVACMode.HEAT: "hot", HVACMode.FAN_ONLY: "wind"}
# climate_mode as reported by the app. German values are accepted for apps before 0.13.
FROM_CAR = {"cool": HVACMode.COOL, "cooling": HVACMode.COOL, "heat": HVACMode.HEAT, "heating": HVACMode.HEAT,
            "fan": HVACMode.FAN_ONLY, "vent": HVACMode.FAN_ONLY, "ventilation": HVACMode.FAN_ONLY,
            "kuehlen": HVACMode.COOL, "heizen": HVACMode.HEAT, "lueften": HVACMode.FAN_ONLY}


async def async_setup_entry(hass, entry, async_add_entities):
    add_vehicles(entry, async_add_entities, lambda c, vin: [GatewayClimate(c, vin, "climate_control")])


class GatewayClimate(GatewayEntity, ClimateEntity):
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.FAN_ONLY]
    _attr_fan_modes = [str(i) for i in range(1, 8)]
    _attr_min_temp, _attr_max_temp, _attr_target_temperature_step = 18, 32, 1
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = F.TARGET_TEMPERATURE | F.FAN_MODE | F.TURN_ON | F.TURN_OFF

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, f"{DOMAIN}_preset_{self._vin}", self.async_write_ha_state))

    @property
    def _preset(self) -> dict:
        return self.coordinator.preset(self._vin)

    @property
    def hvac_mode(self):
        if not self.values.get("climate"):
            return HVACMode.OFF
        return FROM_CAR.get(self.values.get("climate_mode"), HVACMode.FAN_ONLY)

    @property
    def target_temperature(self):
        return self.values.get("target_temp_left") if self.values.get("climate") else self._preset["temperature"]

    @property
    def fan_mode(self):
        speed = self.values.get("fan_speed") if self.values.get("climate") else self._preset["fan_speed"]
        return str(speed) if speed else None

    @property
    def current_temperature(self):
        return self.values.get("inside_temp")

    async def _on(self):
        await self._command("climate_on", self.coordinator.climate_params(self._vin))

    async def _off(self):
        # with the preset temperature: the car takes it as its target without starting the climate
        await self._command("climate_off", {"temperature": int(self._preset["temperature"])})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._off()
            return
        self.coordinator.set_preset(self._vin, mode=MODES[hvac_mode])
        await self._on()

    async def async_turn_on(self) -> None:
        await self._on()

    async def async_turn_off(self) -> None:
        await self._off()

    async def async_set_temperature(self, **kwargs) -> None:
        if ATTR_TEMPERATURE in kwargs:
            self.coordinator.set_preset(self._vin, temperature=int(kwargs[ATTR_TEMPERATURE]))
        if kwargs.get("hvac_mode") and kwargs["hvac_mode"] != HVACMode.OFF:
            self.coordinator.set_preset(self._vin, mode=MODES[kwargs["hvac_mode"]])
        if self.hvac_mode != HVACMode.OFF or kwargs.get("hvac_mode"):
            await self._on()
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self.coordinator.set_preset(self._vin, fan_speed=int(fan_mode))
        if self.hvac_mode != HVACMode.OFF:
            await self._on()
        self.async_write_ha_state()
