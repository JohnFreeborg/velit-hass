"""Sensor entities for Velit heater and AC devices.

Heater sensors (all sourced from coordinator data):
  - Inlet temperature
  - Altitude
  - Fault code (human-readable string)
  - Machine state (human-readable string)

AC sensors:
  - Inlet temperature (always Celsius — AC protocol encodes inlet as raw °C)
  - Fault code (human-readable string)

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
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_TYPE_AC, DEVICE_TYPE_HEATER, DOMAIN
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
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
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

# Sensor descriptors for all AC sensors.
AC_SENSORS: tuple[VelitSensorEntityDescription, ...] = (
    VelitSensorEntityDescription(
        key="inlet_temp",
        data_key="inlet_temp_c",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        # AC inlet temp is always encoded as raw Celsius in the protocol.
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    VelitSensorEntityDescription(
        key="fault_code",
        data_key="fault_name",
        name="Fault",
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
    elif entry.data["device_type"] == DEVICE_TYPE_AC:
        async_add_entities([
            VelitACSensorEntity(coordinator, entry, description)
            for description in AC_SENSORS
        ])


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


class VelitACSensorEntity(CoordinatorEntity[VelitACCoordinator], SensorEntity):
    """A single sensor entity reading from the AC coordinator data dict."""

    entity_description: VelitSensorEntityDescription

    def __init__(
        self,
        coordinator: VelitACCoordinator,
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

    @property
    def native_value(self) -> float | int | str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.entity_description.data_key)
