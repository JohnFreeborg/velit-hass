"""Switch entities for Velit heater and AC devices.

Heater:
  VelitHeaterBLESwitch — toggles the BLE connection on/off. On = connected
    and polling; off = disconnected and not polling. Turning off suppresses
    the reconnect loop so the device stays released until the user turns it
    back on. Useful for handing the device to the Velit mobile app without
    restarting the integration.

AC:
  No switch entities at this time.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, UnitOfTime
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
    """Set up Velit switch entities from a config entry."""
    if entry.data["device_type"] != DEVICE_TYPE_HEATER:
        return

    coordinator: VelitHeaterCoordinator = entry.runtime_data
    async_add_entities([
        VelitHeaterBLESwitch(coordinator, entry),
        VelitHeaterFuelPrimingSwitch(coordinator, entry),
    ])


class VelitHeaterBLESwitch(CoordinatorEntity[VelitHeaterCoordinator], SwitchEntity):
    """Toggle switch for the heater BLE connection.

    On  — BLE connected, coordinator polling normally.
    Off — BLE disconnected, reconnect loop suppressed. The device is free
          for other apps (e.g. the Velit mobile app) to use.

    Turning the switch back on triggers an immediate connect attempt. If it
    fails (device busy), the switch stays off and the user can retry.
    """

    _attr_name = "BLE Connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:bluetooth"

    def __init__(
        self,
        coordinator: VelitHeaterCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data['address']}_ble_connection"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=entry.data.get(CONF_NAME, entry.data["address"]),
            manufacturer="Velit",
        )

    @property
    def available(self) -> bool:
        # The switch reflects raw BLE connection state — it must remain
        # interactive even when the coordinator cannot poll (i.e. when the
        # user has turned it off). Returning True unconditionally keeps it
        # tappable so the user can reconnect.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator._client.connected

    async def async_turn_on(self, **kwargs) -> None:
        """Connect to the device and resume polling."""
        try:
            await self.coordinator._client.connect()
            await self.coordinator.async_request_refresh()
        except Exception as exc:
            _LOGGER.warning("BLE reconnect failed: %s", exc)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Disconnect from the device and suppress automatic reconnection."""
        await self.coordinator._client.disconnect()
        self.async_write_ha_state()


_PRIME_DURATION = 30  # seconds — matches physical hardware button auto-stop


class VelitHeaterFuelPrimingSwitch(CoordinatorEntity[VelitHeaterCoordinator], SwitchEntity):
    """Toggle switch for the fuel pump prime cycle.

    On  — prime cycle running; auto-stops after 30 seconds.
    Off — idle; turning off early sends the stop command immediately.

    Prime state is stored on the coordinator so the companion countdown
    sensor can read it without coupling directly to this entity.
    """

    _attr_name = "Fuel Pump Prime"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:fuel"

    def __init__(
        self,
        coordinator: VelitHeaterCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data['address']}_fuel_priming"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=entry.data.get(CONF_NAME, entry.data["address"]),
            manufacturer="Velit",
        )
        self._prime_task: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        # Readable and pressable whenever BLE is connected.
        return self.coordinator._client.connected

    @property
    def is_on(self) -> bool:
        return self.coordinator.priming

    async def async_turn_on(self, **kwargs) -> None:
        """Start the fuel pump prime cycle."""
        if self.coordinator.priming:
            return
        await self.coordinator._client.send_command(0x05, bytes([0x00]))
        self.coordinator.priming = True
        self.coordinator.prime_remaining = _PRIME_DURATION
        self._prime_task = asyncio.create_task(self._run_prime())
        self.async_write_ha_state()
        self.coordinator._notify_prime_tick()

    async def async_turn_off(self, **kwargs) -> None:
        """Stop the prime cycle early — cancels the task which sends the stop command."""
        if not self.coordinator.priming:
            return
        if self._prime_task and not self._prime_task.done():
            self._prime_task.cancel()

    async def _run_prime(self) -> None:
        """Countdown task — ticks every second, sends stop on completion or cancellation."""
        try:
            while self.coordinator.prime_remaining > 0:
                await asyncio.sleep(1)
                self.coordinator.prime_remaining -= 1
                self.async_write_ha_state()
                self.coordinator._notify_prime_tick()
            await self.coordinator._client.send_command(0x06, bytes([0x00]))
            _LOGGER.debug("Fuel pump prime auto-stopped after %ds", _PRIME_DURATION)
        except asyncio.CancelledError:
            await self.coordinator._client.send_command(0x06, bytes([0x00]))
            _LOGGER.debug("Fuel pump prime stopped early by user")
        finally:
            self.coordinator.priming = False
            self.coordinator.prime_remaining = 0
            self._prime_task = None
            self.async_write_ha_state()
            self.coordinator._notify_prime_tick()
