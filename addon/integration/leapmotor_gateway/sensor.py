"""Sensors from the values of the app."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import (PERCENTAGE, UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfEnergy, UnitOfLength,
                                 UnitOfPower, UnitOfPressure, UnitOfSpeed, UnitOfTemperature, UnitOfTime)
from homeassistant.components.zone import async_active_zone
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .entity import GatewayEntity, add_vehicles

M, T = SensorStateClass.MEASUREMENT, SensorStateClass.TOTAL_INCREASING


@dataclass(frozen=True, kw_only=True)
class Description(SensorEntityDescription):
    source: str = "values"         # values | extras | gateway | trip (trip computer) | derived
    value: Callable[[dict], Any] | None = None      # trip and derived: computed from the vehicle data


def _trip(part: str, field: str, factor: float = 1) -> Callable[[dict], Any]:
    """Value from the trip computer summary of the app, for example trip.km."""
    def read(v: dict):
        d = (v.get("trip_computer") or {}).get(part) or {}
        return None if d.get(field) is None else (round(d[field] * factor) if factor != 1 else d[field])
    return read


def _state(v: dict) -> str | None:
    w = v.get("values") or {}
    if not w:
        return None
    if w.get("ready"):
        return "driving"
    if w.get("charging"):
        return "charging"
    return "plugged_in" if w.get("ac_cable") or w.get("dc_cable") else "parked"


TYRES = {"fl": "front_left", "fr": "front_right", "rl": "rear_left", "rr": "rear_right"}


def _tyres(v: dict) -> dict | None:
    """The four pressures and whether to warn: below 2.3 or above 3.2 bar, or more than 0.2 bar apart per axle."""
    w = v.get("values") or {}
    d = {k: w.get(f"tyre_{k}") for k in TYRES}
    if any(x is None for x in d.values()):
        return None
    warning = any(not 2.3 <= x <= 3.2 for x in d.values()) or abs(d["fl"] - d["fr"]) > 0.2 or abs(d["rl"] - d["rr"]) > 0.2
    return {**{TYRES[k]: x for k, x in d.items()}, "warning": warning, "lowest": min(d.values())}


def _charge_end(v: dict):
    """Forecast: time of the vehicle data plus the charge time left reported by the car. The car includes
    its charging curve and any charge limit. Stable between polls."""
    w = v.get("values") or {}
    left, when = w.get("charge_time_left"), dt_util.parse_datetime(w.get("vehicle_time") or "")
    if not w.get("charging") or not left or when is None:
        return None
    return when + timedelta(minutes=left)


def _energy_to_charge(v: dict):
    """kWh until the end: charging power times charge time left, at most up to 100 % (capacity from energy and SoC)."""
    w = v.get("values") or {}
    left, power, soc, energy = w.get("charge_time_left"), w.get("charging_power"), w.get("battery"), w.get("energy")
    if not w.get("charging") or not left or not power:
        return None
    kwh = power * left / 60
    if soc and energy:
        kwh = min(kwh, energy / soc * (100 - soc))
    return round(kwh, 1)


KM, KWH, CONSUMPTION = UnitOfLength.KILOMETERS, UnitOfEnergy.KILO_WATT_HOUR, "kWh/100 km"
GEARS = ["park", "drive", "neutral", "reverse"]
CHARGE_STATES = ["not_charging", "charging", "finish", "error", "setting", "regening"]
STATES = ["driving", "charging", "plugged_in", "parked"]
SIDES = ("fl", "fr", "rl", "rr")

D = Description
SENSORS = [
    D(key="battery", native_unit_of_measurement=PERCENTAGE, device_class=SensorDeviceClass.BATTERY, state_class=M),
    D(key="range", native_unit_of_measurement=KM, device_class=SensorDeviceClass.DISTANCE, state_class=M,
      suggested_display_precision=0),
    D(key="energy", native_unit_of_measurement=KWH, device_class=SensorDeviceClass.ENERGY_STORAGE, state_class=M),
    D(key="odometer", native_unit_of_measurement=KM, device_class=SensorDeviceClass.DISTANCE, state_class=T,
      suggested_display_precision=0),
    D(key="speed", native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR, device_class=SensorDeviceClass.SPEED, state_class=M),
    D(key="gear", icon="mdi:car-shift-pattern", device_class=SensorDeviceClass.ENUM, options=GEARS),
    D(key="charge_state", icon="mdi:ev-station", device_class=SensorDeviceClass.ENUM, options=CHARGE_STATES),
    D(key="charge_time_left", native_unit_of_measurement=UnitOfTime.MINUTES, device_class=SensorDeviceClass.DURATION),
    D(key="charging_power", native_unit_of_measurement=UnitOfPower.KILO_WATT, device_class=SensorDeviceClass.POWER, state_class=M),
    D(key="battery_current", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=M),
    D(key="battery_voltage", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=M),
    D(key="battery_temp_min", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=M),
    D(key="outside_temp", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=M),
    D(key="target_temp_left", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE),
    D(key="fan_speed", icon="mdi:fan"),
    D(key="sunshade", icon="mdi:blinds", native_unit_of_measurement=PERCENTAGE),
    *[D(key=f"window_{s}", native_unit_of_measurement=PERCENTAGE, icon="mdi:car-door") for s in SIDES],
    *[D(key=f"tyre_{s}", native_unit_of_measurement=UnitOfPressure.BAR, device_class=SensorDeviceClass.PRESSURE,
        state_class=M, suggested_display_precision=2) for s in SIDES],
    D(key="vehicle_time", device_class=SensorDeviceClass.TIMESTAMP),
    # hourly from the cloud
    D(key="today_driving_percent", native_unit_of_measurement=PERCENTAGE, source="extras", icon="mdi:car"),
    D(key="today_climate_percent", native_unit_of_measurement=PERCENTAGE, source="extras", icon="mdi:air-conditioner"),
    D(key="today_other_percent", native_unit_of_measurement=PERCENTAGE, source="extras", icon="mdi:dots-horizontal"),
    D(key="week_driving_percent", native_unit_of_measurement=PERCENTAGE, source="extras", icon="mdi:car"),
    D(key="week_climate_percent", native_unit_of_measurement=PERCENTAGE, source="extras", icon="mdi:air-conditioner"),
    D(key="week_other_percent", native_unit_of_measurement=PERCENTAGE, source="extras", icon="mdi:dots-horizontal"),
    D(key="total_energy_kwh", native_unit_of_measurement=KWH, device_class=SensorDeviceClass.ENERGY, state_class=T, source="extras"),
    D(key="distance_7_days_km", native_unit_of_measurement=KM, device_class=SensorDeviceClass.DISTANCE, source="extras"),
    D(key="consumption_6_weeks", native_unit_of_measurement=CONSUMPTION, source="extras", icon="mdi:speedometer"),
    D(key="messages_unread", source="extras", icon="mdi:message-badge"),
    D(key="last_message", source="extras", icon="mdi:message-text"),
    # setting from the app
    D(key="trip_end_min", native_unit_of_measurement=UnitOfTime.MINUTES, source="gateway", icon="mdi:timer-sand",
      entity_category=EntityCategory.DIAGNOSTIC),
    D(key="plate", source="gateway", icon="mdi:card-text-outline"),
    # lowest of the four pressures, all four and the warning as attributes
    D(key="tyre_pressure", source="derived", value=lambda v: (_tyres(v) or {}).get("lowest"),
      native_unit_of_measurement=UnitOfPressure.BAR, device_class=SensorDeviceClass.PRESSURE, suggested_display_precision=2),
    # charging forecast, only while charging
    D(key="charge_end_forecast", source="derived", value=_charge_end, device_class=SensorDeviceClass.TIMESTAMP,
      icon="mdi:timer-sand-complete"),
    D(key="energy_to_charge", source="derived", value=_energy_to_charge, native_unit_of_measurement=KWH,
      device_class=SensorDeviceClass.ENERGY, icon="mdi:battery-charging-medium"),
    D(key="state", source="derived", value=_state, device_class=SensorDeviceClass.ENUM, options=STATES, icon="mdi:car-info"),
    # trip computer of the app: current or last trip, since the last charge, lists
    D(key="trip_distance", source="trip", value=_trip("trip", "km"), native_unit_of_measurement=KM,
      device_class=SensorDeviceClass.DISTANCE, icon="mdi:map-marker-distance"),
    D(key="trip_duration", source="trip", value=_trip("trip", "h", 60), native_unit_of_measurement=UnitOfTime.MINUTES,
      device_class=SensorDeviceClass.DURATION, icon="mdi:timer-outline"),
    D(key="trip_energy", source="trip", value=_trip("trip", "kwh"), native_unit_of_measurement=KWH,
      device_class=SensorDeviceClass.ENERGY, icon="mdi:lightning-bolt"),
    D(key="trip_consumption", source="trip", value=_trip("trip", "consumption"), native_unit_of_measurement=CONSUMPTION,
      icon="mdi:speedometer"),
    D(key="since_charge_distance", source="trip", value=_trip("since_charge", "km"), native_unit_of_measurement=KM,
      device_class=SensorDeviceClass.DISTANCE, icon="mdi:map-marker-distance"),
    D(key="since_charge_energy", source="trip", value=_trip("since_charge", "kwh"), native_unit_of_measurement=KWH,
      device_class=SensorDeviceClass.ENERGY, icon="mdi:lightning-bolt"),
    D(key="since_charge_consumption", source="trip", value=_trip("since_charge", "consumption"),
      native_unit_of_measurement=CONSUMPTION, icon="mdi:speedometer"),
    D(key="last_charge", source="trip",
      value=lambda v: ((v.get("trip_computer") or {}).get("charges") or [{}])[0].get("charged_kwh"),
      native_unit_of_measurement=KWH, device_class=SensorDeviceClass.ENERGY, icon="mdi:ev-station"),
    D(key="charges", source="trip", value=lambda v: (v.get("trip_computer") or {}).get("charge_count"),
      icon="mdi:format-list-numbered"),
    D(key="trips", source="trip", value=lambda v: (v.get("trip_computer") or {}).get("trip_count"),
      icon="mdi:format-list-numbered"),
    # the gateway itself
    D(key="status", source="gateway", icon="mdi:router-wireless"),
    D(key="approvals_pending", source="gateway", icon="mdi:shield-key"),
    D(key="last_command", source="gateway", icon="mdi:history"),
]


async def async_setup_entry(hass, entry, async_add_entities):
    add_vehicles(entry, async_add_entities, lambda c, vin: [GatewaySensor(c, vin, d) for d in SENSORS])


class GatewaySensor(GatewayEntity, SensorEntity):
    entity_description: Description

    def __init__(self, coordinator, vin: str, description: Description) -> None:
        super().__init__(coordinator, vin, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if self.entity_description.source in ("gateway", "trip"):
            return self.coordinator.last_update_success
        return super().available

    @property
    def native_value(self):
        source, key = self.entity_description.source, self.entity_description.key
        if self.entity_description.value is not None:
            return self.entity_description.value(self.vehicle)
        if source == "gateway":
            v = self.vehicle
            if key == "last_command":
                c = v.get("last_command") or {}
                return f"{c.get('action')}: {c.get('result')}"[:250] if c else "none"
            return v.get(key)
        value = (self.extras if source == "extras" else self.values).get(key)
        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP and value:
            return dt_util.parse_datetime(value)
        if self.entity_description.device_class == SensorDeviceClass.ENUM and value not in (self.entity_description.options or []):
            return None                                   # unknown value from the car: empty rather than an error
        return value

    @property
    def icon(self):
        if self.entity_description.key == "tyre_pressure":
            return "mdi:car-tire-alert" if (_tyres(self.vehicle) or {}).get("warning") else "mdi:tire"
        return super().icon

    @property
    def extra_state_attributes(self):
        key = self.entity_description.key
        if key == "last_command":
            return self.vehicle.get("last_command") or {}
        if key == "last_message":
            return {"time": self.extras.get("last_message_time")}
        if key == "tyre_pressure":
            return {k: v for k, v in (_tyres(self.vehicle) or {}).items() if k != "lowest"} or None
        trip = self.vehicle.get("trip_computer") or {}
        if key == "trip_distance" and trip.get("trip"):
            return {k: trip["trip"].get(k) for k in ("ongoing", "start", "end")}
        if key == "last_charge" and trip.get("charges"):
            return self._with_place(trip["charges"][0])
        if key == "charges":
            return {"items": [self._with_place(c) for c in trip.get("charges") or []]}
        if key == "trips":
            return {"items": trip.get("trips") or []}
        return None

    def _with_place(self, charge: dict) -> dict:
        """Place of a charge = the zone at its position, as the tracker shows it. Tariffs depend on it."""
        place = None
        if charge.get("lat") is not None and charge.get("lon") is not None:
            zone = async_active_zone(self.hass, charge["lat"], charge["lon"], 30)
            place = zone.name if zone else None
        return {**charge, "place": place}
