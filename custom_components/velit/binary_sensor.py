"""Binary sensor entities for Velit heater and AC devices.

Heater:
  VelitHeaterFaultBinarySensor — on when any fault code is active; off when clear.
    Exposed as non-diagnostic so it appears prominently on the device page and
    can be used directly in automations and dashboard cards without extra setup.

AC:
  No binary sensors until the AC fault response format is confirmed via hardware
  capture and a decoder is implemented.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_TYPE_HEATER, DOMAIN
from .coordinator import VelitHeaterCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Velit binary sensor entities from a config entry."""
    if entry.data["device_type"] != DEVICE_TYPE_HEATER:
        return

    coordinator: VelitHeaterCoordinator = entry.runtime_data
    async_add_entities([VelitHeaterFaultBinarySensor(coordinator, entry)])


class VelitHeaterFaultBinarySensor(
    CoordinatorEntity[VelitHeaterCoordinator], BinarySensorEntity
):
    """Binary sensor that is on when any heater fault code is active.

    Paired with the Repairs issue raised by the coordinator, this gives users
    two ways to react to a fault: a persistent Repairs alert and an entity
    suitable for automations and dashboard cards.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Fault Active"

    def __init__(
        self,
        coordinator: VelitHeaterCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data['address']}_fault_active"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=entry.data.get(CONF_NAME, entry.data["address"]),
            manufacturer="Velit",
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("fault_code", 0) != 0

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        fault_code = self.coordinator.data.get("fault_code", 0)
        if fault_code == 0:
            return {}
        return {
            "fault_code": fault_code,
            "fault_name": self.coordinator.data.get("fault_name"),
        }
