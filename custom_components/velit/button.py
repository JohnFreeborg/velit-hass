"""Button entities for Velit heater devices.

Exposes one-shot maintenance commands as HA button entities.

Buttons:
  VelitHeaterCleaningButton  — triggers residual fuel clearance (0x09),
      a one-shot command; the device manages the cycle and reports progress
      via machine_state (4 = Cleaning, 5 = Clean Complete).

Note: Fuel pump priming was a button but is now a switch entity in switch.py
to provide on/off state and a companion countdown sensor.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
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
    """Set up Velit button entities from a config entry."""
    if entry.data["device_type"] != DEVICE_TYPE_HEATER:
        return

    coordinator: VelitHeaterCoordinator = entry.runtime_data
    async_add_entities([VelitHeaterCleaningButton(coordinator, entry)])


class VelitHeaterCleaningButton(
    CoordinatorEntity[VelitHeaterCoordinator], ButtonEntity
):
    """Trigger residual fuel clearance (cleaning mode).

    Sends func 0x09 — a one-shot command that purges residual fuel from the
    system. No stop command is required; the device manages the sequence.
    """

    _attr_name = "Cleaning"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:broom"

    def __init__(
        self,
        coordinator: VelitHeaterCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data['address']}_cleaning"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=entry.data.get(CONF_NAME, entry.data["address"]),
            manufacturer="Velit",
        )

    async def async_press(self) -> None:
        """Send the residual fuel clearance command."""
        await self.coordinator._client.send_command(0x09, bytes([0x00]))
        _LOGGER.debug("Residual fuel clearance command sent")
