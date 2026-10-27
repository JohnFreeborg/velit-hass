"""Switch entities for Velit heater and AC devices.

Heater:
  VelitHeaterBLESwitch — toggles the BLE connection on/off. On = connected
    and polling; off = disconnected and not polling. Turning off suppresses
    the reconnect loop so the device stays released until the user turns it
    back on. Useful for handing the device to the Velit mobile app without
    restarting the integration.

AC:
  VelitACBLESwitch — same behaviour as the heater BLE switch. BLE allows
    only a single connection; releasing the connection lets the Velit mobile
    app pair while keeping the integration entry intact.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_TYPE_AC, DEVICE_TYPE_HEATER, DOMAIN
from .coordinator import VelitACCoordinator, VelitHeaterCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Velit switch entities from a config entry."""
    if entry.data["device_type"] == DEVICE_TYPE_HEATER:
        coordinator: VelitHeaterCoordinator = entry.runtime_data
        async_add_entities([
            VelitHeaterBLESwitch(coordinator, entry),
            VelitHeaterFuelPrimingSwitch(coordinator, entry),
            VelitHeaterCleaningSwitch(coordinator, entry),
        ])
    elif entry.data["device_type"] == DEVICE_TYPE_AC:
        ac_coordinator: VelitACCoordinator = entry.runtime_data
        async_add_entities([
            VelitACBLESwitch(ac_coordinator, entry),
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
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Stop the prime cycle early — cancels the task which sends the stop command."""
        if not self.coordinator.priming:
            return
        if self._prime_task and not self._prime_task.done():
            self._prime_task.cancel()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a running prime cycle and wait for its cleanup to finish.

        Awaiting here lets the task send the stop command and reset coordinator
        state while this entity is still registered, instead of ticking on after
        removal.
        """
        if self._prime_task and not self._prime_task.done():
            self._prime_task.cancel()
            try:
                await self._prime_task
            except asyncio.CancelledError:
                pass

    async def _run_prime(self) -> None:
        """Countdown task — ticks every second, sends stop on completion or cancellation."""
        cancelled = False
        try:
            while self.coordinator.prime_remaining > 0:
                await asyncio.sleep(1)
                self.coordinator.prime_remaining -= 1
                self.async_write_ha_state()
                self.coordinator._notify_prime_tick()
            await self.coordinator._client.send_command(0x06, bytes([0x00]))
            _LOGGER.debug("Fuel pump prime auto-stopped after %ds", _PRIME_DURATION)
        except asyncio.CancelledError:
            cancelled = True
            await self.coordinator._client.send_command(0x06, bytes([0x00]))
            _LOGGER.debug("Fuel pump prime stopped early")
        finally:
            self.coordinator.priming = False
            self.coordinator.prime_remaining = 0
            self._prime_task = None
            self.async_write_ha_state()
            self.coordinator._notify_prime_tick()
            await self.coordinator.async_request_refresh()
        if cancelled:
            raise asyncio.CancelledError


class VelitHeaterCleaningSwitch(CoordinatorEntity[VelitHeaterCoordinator], SwitchEntity):
    """Switch for the residual fuel cleaning cycle.

    On  — cleaning cycle running; auto-turns off when machine_state returns
          to Standby (coordinator clears the flag on the next successful poll).
    Off — idle; tapping starts the cycle.

    The switch cannot be turned off manually — there is no stop command for the
    cleaning cycle. Attempts to turn it off while on are ignored and the state
    is pushed back immediately so the UI snaps back to on.
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

    @property
    def available(self) -> bool:
        return self.coordinator._client.connected

    @property
    def is_on(self) -> bool:
        return self.coordinator.cleaning

    async def async_turn_on(self, **kwargs) -> None:
        """Send the cleaning command and mark cycle as active."""
        if self.coordinator.cleaning:
            return
        await self.coordinator._client.send_command(0x09, bytes([0x00]))
        self.coordinator.start_cleaning()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """No-op — the cleaning cycle cannot be stopped manually.

        Pushes state back immediately so the UI snaps back to on rather than
        appearing to accept the off command.
        """
        self.async_write_ha_state()


class VelitACBLESwitch(CoordinatorEntity[VelitACCoordinator], SwitchEntity):
    """Toggle switch for the AC BLE connection.

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
        coordinator: VelitACCoordinator,
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
