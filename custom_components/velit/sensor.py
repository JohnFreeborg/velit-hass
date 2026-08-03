"""Sensor entities for Velit heater and AC devices.

Heater sensors (all sourced from coordinator data):
  - Inlet temperature
  - Casing temperature
  - Outlet temperature
  - Supply voltage
  - Fan RPM
  - Altitude
  - Fuel pump frequency (Hz)
  - Heater power (W)
  - Fault code (human-readable string)
  - Machine state (human-readable string)

AC sensors:
  - Fault info (raw bytes, pending format confirmation via hardware capture)

All temperature sensors report in Celsius — the coordinator converts from
the device's active unit before storing values in the data dict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_TYPE_HEATER, DOMAIN
from .coordinator import VelitACCoordinator, VelitHeaterCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class VelitSensorEntityDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with the coordinator data key."""
    data_key: str = ""


# Sensor descriptors for all heater sensors.
HEATER_SENSORS: tuple[VelitSensorEntityDescription, ...] = (
    VelitSensorEntityDescription(
        key="inlet_temp",
        data_key="inlet_temp_c",
        name="Inlet Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    VelitSensorEntityDescription(
        key="casing_temp",
        data_key="casing_temp_c",
        name="Casing Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    VelitSensorEntityDescription(
        key="outlet_temp",
        data_key="outlet_temp_c",
        name="Outlet Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    VelitSensorEntityDescription(
        key="voltage",
        data_key="voltage_v",
        name="Supply Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    VelitSensorEntityDescription(
        key="fan_rpm",
        data_key="fan_rpm",
        name="Fan Speed",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="RPM",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    VelitSensorEntityDescription(
        key="altitude",
        data_key="altitude",
        name="Altitude",
        state_class=SensorStateClass.MEASUREMENT,
        # Unit depends on the device's active temperature unit (metric vs imperial).
        # Set dynamically in VelitHeaterSensorEntity based on coordinator.temp_unit.
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    VelitSensorEntityDescription(
        key="fuel_pump_freq",
        data_key="fuel_pump_hz",
        name="Fuel Pump Frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    VelitSensorEntityDescription(
        key="heater_power",
        data_key="heater_power_w",
        name="Heater Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    VelitSensorEntityDescription(
        key="fault_code",
        data_key="fault_name",
        name="Fault",
    ),
    VelitSensorEntityDescription(
        key="machine_state",
        data_key="machine_state_str",
        name="Machine State",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Velit sensor entities from a config entry."""
    coordinator = entry.runtime_data

    if entry.data["device_type"] == DEVICE_TYPE_HEATER:
        async_add_entities([
            *(VelitHeaterSensorEntity(coordinator, entry, description)
              for description in HEATER_SENSORS),
            VelitHeaterPrimeCountdownSensor(coordinator, entry),
        ])
    else:
        async_add_entities([VelitACFaultSensorEntity(coordinator, entry)])


class VelitHeaterSensorEntity(CoordinatorEntity[VelitHeaterCoordinator], SensorEntity):
    """A single sensor entity reading from the heater coordinator data dict."""

    entity_description: VelitSensorEntityDescription

    def __init__(
        self,
        coordinator: VelitHeaterCoordinator,
        entry: ConfigEntry,
        description: VelitSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data['address']}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=entry.data.get(CONF_NAME, entry.data["address"]),
            manufacturer="Velit",
        )
        # Altitude unit is set once the coordinator has detected the device unit.
        if description.key == "altitude":
            self._attr_native_unit_of_measurement = (
                "ft" if coordinator.temp_unit == UnitOfTemperature.FAHRENHEIT else "m"
            )

    @property
    def native_value(self) -> float | int | str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.entity_description.data_key)


class VelitHeaterPrimeCountdownSensor(SensorEntity):
    """Counts down seconds remaining in the fuel pump prime cycle.

    Updated every second by the prime switch's tick callbacks rather than by
    the coordinator poll, so the display ticks in real time during the 30s
    prime sequence. Reads prime state from the coordinator so it shares state
    with the switch without any direct entity-to-entity coupling.
    """

    _attr_name = "Fuel Pump Prime Remaining"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: VelitHeaterCoordinator,
        entry: ConfigEntry,
    ) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.data['address']}_prime_countdown"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=entry.data.get(CONF_NAME, entry.data["address"]),
            manufacturer="Velit",
        )
        # Register for per-second ticks from the prime switch.
        coordinator.register_prime_tick(self.async_write_ha_state)

    @property
    def native_value(self) -> int:
        return self._coordinator.prime_remaining


class VelitACFaultSensorEntity(CoordinatorEntity[VelitACCoordinator], SensorEntity):
    """Exposes raw AC fault query response pending format confirmation.

    The AC protocol document lists func 0x0B as 'System Fault Info' but does
    not provide a response format or fault code table. This sensor stores the
    raw response bytes as a hex string until the format is confirmed via
    hardware capture and a proper decoder can be added.
    """

    _attr_name = "Fault Info (raw)"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: VelitACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data['address']}_fault_raw"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=entry.data.get(CONF_NAME, entry.data["address"]),
            manufacturer="Velit",
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.get("fault_raw")
        if raw is None:
            return None
        return raw.hex()
