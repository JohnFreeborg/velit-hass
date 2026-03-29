"""Button entities for Velit heater devices.

Exposes one-shot and toggled maintenance commands as HA button entities.
These are diagnostic/advanced controls not needed for normal operation.

Buttons:
  VelitHeaterPrimeFuelPumpButton  — starts fuel pump (0x05), auto-stops after
      30 seconds (0x06). Pressing again while priming stops it immediately,
      mirroring the single-button behaviour on the physical hardware interface.
  VelitHeaterCleaningButton       — triggers residual fuel clearance (0x09),
      a one-shot command with no stop counterpart.
"""

from __future__ import annotations

import asyncio
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

# Duration in seconds before the prime sequence auto-stops, matching the
# default behaviour of the physical hardware button.
_PRIME_AUTO_STOP_SECONDS = 30


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Velit button entities from a config entry."""
    if entry.data["device_type"] != DEVICE_TYPE_HEATER:
        return

    coordinator: VelitHeaterCoordinator = entry.runtime_data
    async_add_entities([
        VelitHeaterPrimeFuelPumpButton(coordinator, entry),
        VelitHeaterCleaningButton(coordinator, entry),
    ])


class VelitHeaterPrimeFuelPumpButton(
    CoordinatorEntity[VelitHeaterCoordinator], ButtonEntity
):
    """Prime the fuel pump.

    First press sends func 0x05 (start pump) and schedules an automatic stop
    (func 0x06) after 30 seconds. Pressing again while priming cancels the
    scheduled stop and sends 0x06 immediately, matching the physical interface.
    """

    _attr_name = "Prime Fuel Pump"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:fuel"

    def __init__(
        self,
        coordinator: VelitHeaterCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data['address']}_prime_fuel_pump"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=entry.data.get(CONF_NAME, entry.data["address"]),
            manufacturer="Velit",
        )
        self._prime_task: asyncio.Task | None = None

    async def async_press(self) -> None:
        """Handle button press — start or stop priming."""
        if self._prime_task and not self._prime_task.done():
            # Already priming — cancel the scheduled stop and stop immediately.
            self._prime_task.cancel()
            self._prime_task = None
            await self.coordinator._client.send_command(0x06, bytes([0x00]))
            _LOGGER.debug("Fuel pump prime stopped early by user")
            return

        # Start priming and schedule auto-stop.
        await self.coordinator._client.send_command(0x05, bytes([0x00]))
        _LOGGER.debug(
            "Fuel pump prime started; auto-stop in %ds", _PRIME_AUTO_STOP_SECONDS
        )
        self._prime_task = asyncio.create_task(self._auto_stop())

    async def _auto_stop(self) -> None:
        """Wait then send stop command; cancelled if user presses early."""
        await asyncio.sleep(_PRIME_AUTO_STOP_SECONDS)
        await self.coordinator._client.send_command(0x06, bytes([0x00]))
        self._prime_task = None
        _LOGGER.debug("Fuel pump prime auto-stopped after %ds", _PRIME_AUTO_STOP_SECONDS)


class VelitHeaterCleaningButton(
    CoordinatorEntity[VelitHeaterCoordinator], ButtonEntity
):
    """Trigger residual fuel clearance (cleaning mode).

    Sends func 0x09 — a one-shot command that purges residual fuel from the
    system. No stop command is required; the device manages the sequence.
    """

    _attr_name = "Clean (Residual Fuel Clearance)"
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
