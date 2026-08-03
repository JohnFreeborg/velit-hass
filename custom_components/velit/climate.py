"""Climate entities for Velit heater and AC devices.

One entity class per device type — the protocols and capability sets are
different enough that a shared class would obscure more than it unifies.

Both entities read state exclusively from their coordinator's data dict and
never issue queries themselves. Actions send a command via the coordinator's
client then trigger an immediate coordinator refresh so state reflects the
confirmed device response without waiting for the next poll cycle.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_UNAVAILABLE_ON_FAULT,
    DEVICE_TYPE_HEATER,
    DOMAIN,
    HEATER_MAX_TEMP_C,
    HEATER_MIN_TEMP_C,
    AC_MAX_TEMP_C,
    AC_MIN_TEMP_C,
)
from .coordinator import VelitACCoordinator, VelitHeaterCoordinator
from .packet_utils import celsius_to_fahrenheit

_LOGGER = logging.getLogger(__name__)

# Heater work mode codes (Query 1 response, work_mode field).
_HEATER_MODE_MANUAL = 1
_HEATER_MODE_THERMOSTAT = 2

# AC operation mode codes (func 0x02).
_AC_MODE_COOL = 1
_AC_MODE_HEAT = 2
_AC_MODE_FAN = 3
_AC_MODE_ENERGY_SAVING = 4
_AC_MODE_SLEEP = 5
_AC_MODE_TURBO = 6
_AC_MODE_DEHUMIDIFY = 7
_AC_MODE_VENT = 8

# Preset names used in HA for AC modes that don't map directly to HVACMode.
AC_PRESET_NONE = "none"
AC_PRESET_ENERGY_SAVING = "energy_saving"
AC_PRESET_SLEEP = "sleep"
AC_PRESET_TURBO = "turbo"

# Fan modes exposed to HA — string labels matching gear/speed numbers.
FAN_MODES = ["1", "2", "3", "4", "5"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Velit climate entity from a config entry."""
    coordinator = entry.runtime_data

    if entry.data["device_type"] == DEVICE_TYPE_HEATER:
        async_add_entities([VelitHeaterClimateEntity(coordinator, entry)])
    else:
        async_add_entities([VelitACClimateEntity(coordinator, entry)])


class VelitHeaterClimateEntity(CoordinatorEntity[VelitHeaterCoordinator], ClimateEntity):
    """Climate entity for a Velit heater (protocol V1.02).

    HVAC modes:
      OFF   — heater is shut down
      HEAT  — heater running (manual or thermostat preset)

    Presets (only meaningful in HEAT mode):
      manual      — fixed gear, no thermostat
      thermostat  — thermostat controls output

    Fan modes: gear levels 1–5 (only meaningful in manual mode).
    """

    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_preset_modes = ["manual", "thermostat"]
    _attr_fan_modes = FAN_MODES
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = float(HEATER_MIN_TEMP_C)
    _attr_max_temp = float(HEATER_MAX_TEMP_C)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.FAN_MODE
    )

    def __init__(
        self,
        coordinator: VelitHeaterCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.data['address']}_climate"
        self._attr_name = entry.data.get(CONF_NAME, entry.data["address"])
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=entry.data.get(CONF_NAME, entry.data["address"]),
            manufacturer="Velit",
        )

    # ------------------------------------------------------------------
    # State properties — read from coordinator data, never from device
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self._entry.options.get(CONF_UNAVAILABLE_ON_FAULT, False):
            if self.coordinator.data and self.coordinator.data.get("fault_code", 0) != 0:
                return False
        return True

    @property
    def hvac_mode(self) -> HVACMode | None:
        if self.coordinator.data is None:
            return None
        state = self.coordinator.data["machine_state"]
        if state == 0:
            return HVACMode.OFF
        return HVACMode.HEAT

    @property
    def preset_mode(self) -> str | None:
        if self.coordinator.data is None:
            return None
        work_mode = self.coordinator.data["work_mode"]
        if work_mode == _HEATER_MODE_THERMOSTAT:
            return "thermostat"
        return "manual"

    @property
    def current_temperature(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("inlet_temp_c")

    @property
    def target_temperature(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("set_temp_c")

    @property
    def fan_mode(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return str(self.coordinator.data["current_gear"])

    @property
    def hvac_action(self) -> HVACAction | None:
        """Current action shown on the climate card.

        Maps machine state and fault code to an HVACAction so the climate card
        reflects what the device is doing, not just what mode it is set to.
        """
        if self.coordinator.data is None:
            return None
        # Any active fault — device is not operational.
        if self.coordinator.data["fault_code"] != 0:
            return HVACAction.OFF
        state = self.coordinator.data["machine_state"]
        if state == 1:
            return HVACAction.HEATING
        if state == 2:
            # Fan running to cool combustion chamber after shutdown.
            return HVACAction.FAN
        # Standby, overtemp standby, cleaning, clean complete — no active output.
        return HVACAction.IDLE

    @property
    def icon(self) -> str:
        """Return alert icon when a fault is active, thermostat icon otherwise."""
        if self.coordinator.data and self.coordinator.data.get("fault_code", 0) != 0:
            return "mdi:alert-circle"
        return "mdi:thermostat"

    @property
    def extra_state_attributes(self) -> dict:
        """Expose machine state and fault as automation-accessible attributes."""
        if self.coordinator.data is None:
            return {}
        return {
            "machine_state": self.coordinator.data.get("machine_state_str"),
            "fault": self.coordinator.data.get("fault_name"),
        }

    # ------------------------------------------------------------------
    # Actions — send command then refresh to confirm state
    # ------------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator._client.send_command(0x02, bytes([0x00]))
        elif hvac_mode == HVACMode.HEAT:
            # Start in the currently selected preset mode.
            mode_byte = (
                0x02
                if self.preset_mode == "thermostat"
                else 0x01
            )
            await self.coordinator._client.send_command(0x01, bytes([mode_byte]))
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        mode_byte = 0x02 if preset_mode == "thermostat" else 0x01
        await self.coordinator._client.send_command(0x00, bytes([mode_byte]))
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp_c = kwargs.get("temperature")
        if temp_c is None:
            return
        # Send in the device's active unit to avoid flipping the LCD display unit.
        if self.coordinator.temp_unit == UnitOfTemperature.FAHRENHEIT:
            value = round(celsius_to_fahrenheit(temp_c))
        else:
            value = round(temp_c)
        await self.coordinator._client.send_command(0x08, bytes([value]))
        await self.coordinator.async_request_refresh()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        gear = int(fan_mode)
        await self.coordinator._client.send_command(0x07, bytes([gear]))
        await self.coordinator.async_request_refresh()


class VelitACClimateEntity(CoordinatorEntity[VelitACCoordinator], ClimateEntity):
    """Climate entity for a Velit AC unit (protocol V1.01).

    HVAC modes:
      OFF       — power off (func 0x01, data 0x01)
      COOL      — cooling mode
      HEAT      — heating mode
      FAN_ONLY  — fan mode and vent mode both map here (protocols 0x03 and 0x08)
      DRY       — dehumidify mode

    Presets (active within the current HVAC mode):
      none           — standard operation
      energy_saving  — protocol mode 0x04
      sleep          — protocol mode 0x05
      turbo          — protocol mode 0x06

    Note: Fan (0x03) and Vent (0x08) are both mapped to FAN_ONLY — the
    functional difference between them is unconfirmed without hardware testing.
    """

    _attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.FAN_ONLY, HVACMode.DRY]
    _attr_preset_modes = [AC_PRESET_NONE, AC_PRESET_ENERGY_SAVING, AC_PRESET_SLEEP, AC_PRESET_TURBO]
    _attr_fan_modes = FAN_MODES
    _attr_swing_modes = ["off", "on"]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = float(AC_MIN_TEMP_C)
    _attr_max_temp = float(AC_MAX_TEMP_C)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
    )

    # Map from AC protocol mode codes to HA HVACMode.
    _MODE_TO_HVAC: dict[int, HVACMode] = {
        _AC_MODE_COOL: HVACMode.COOL,
        _AC_MODE_HEAT: HVACMode.HEAT,
        _AC_MODE_FAN: HVACMode.FAN_ONLY,
        _AC_MODE_VENT: HVACMode.FAN_ONLY,   # unconfirmed; see class docstring
        _AC_MODE_DEHUMIDIFY: HVACMode.DRY,
    }

    # Preset mode codes — these modify the current HVAC mode rather than replacing it.
    _PRESET_CODES: dict[int, str] = {
        _AC_MODE_ENERGY_SAVING: AC_PRESET_ENERGY_SAVING,
        _AC_MODE_SLEEP: AC_PRESET_SLEEP,
        _AC_MODE_TURBO: AC_PRESET_TURBO,
    }

    def __init__(
        self,
        coordinator: VelitACCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.data['address']}_climate"
        self._attr_name = entry.data.get(CONF_NAME, entry.data["address"])
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=entry.data.get(CONF_NAME, entry.data["address"]),
            manufacturer="Velit",
        )
        # Tracks the last non-preset HVAC mode so we can restore it when
        # clearing a preset (protocol requires resending the base mode code).
        self._last_hvac_mode: HVACMode = HVACMode.COOL

    @property
    def hvac_mode(self) -> HVACMode | None:
        if self.coordinator.data is None:
            return None
        mode_code = self.coordinator.data["mode"]
        if mode_code == 0:
            return HVACMode.OFF
        # Preset codes (4, 5, 6) don't map to an HVACMode directly — return
        # the last known base mode so the UI doesn't flip to an unexpected state.
        if mode_code in self._PRESET_CODES:
            return self._last_hvac_mode
        hvac = self._MODE_TO_HVAC.get(mode_code)
        if hvac is not None:
            self._last_hvac_mode = hvac
        return hvac

    @property
    def preset_mode(self) -> str | None:
        if self.coordinator.data is None:
            return None
        mode_code = self.coordinator.data["mode"]
        return self._PRESET_CODES.get(mode_code, AC_PRESET_NONE)

    @property
    def target_temperature(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("set_temp_c")

    @property
    def fan_mode(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return str(self.coordinator.data["fan_speed"])

    @property
    def swing_mode(self) -> str | None:
        if self.coordinator.data is None:
            return None
        # Protocol: 1 = start swing, 2 = stop swing.
        return "on" if self.coordinator.data["swing"] == 1 else "off"

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator._client.send_command(0x01, bytes([0x01]))
        else:
            # Power on first if currently off, then set mode.
            if self.hvac_mode == HVACMode.OFF:
                await self.coordinator._client.send_command(0x01, bytes([0x02]))
            mode_map = {
                HVACMode.COOL: _AC_MODE_COOL,
                HVACMode.HEAT: _AC_MODE_HEAT,
                HVACMode.FAN_ONLY: _AC_MODE_FAN,
                HVACMode.DRY: _AC_MODE_DEHUMIDIFY,
            }
            code = mode_map.get(hvac_mode)
            if code is not None:
                await self.coordinator._client.send_command(0x02, bytes([code]))
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == AC_PRESET_NONE:
            # Restore the last base HVAC mode.
            mode_map = {
                HVACMode.COOL: _AC_MODE_COOL,
                HVACMode.HEAT: _AC_MODE_HEAT,
                HVACMode.FAN_ONLY: _AC_MODE_FAN,
                HVACMode.DRY: _AC_MODE_DEHUMIDIFY,
            }
            code = mode_map.get(self._last_hvac_mode, _AC_MODE_COOL)
            await self.coordinator._client.send_command(0x02, bytes([code]))
        else:
            preset_map = {
                AC_PRESET_ENERGY_SAVING: _AC_MODE_ENERGY_SAVING,
                AC_PRESET_SLEEP: _AC_MODE_SLEEP,
                AC_PRESET_TURBO: _AC_MODE_TURBO,
            }
            code = preset_map.get(preset_mode)
            if code is not None:
                await self.coordinator._client.send_command(0x02, bytes([code]))
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp_c = kwargs.get("temperature")
        if temp_c is None:
            return
        if self.coordinator.temp_unit == UnitOfTemperature.FAHRENHEIT:
            value = round(celsius_to_fahrenheit(temp_c))
        else:
            value = round(temp_c)
        await self.coordinator._client.send_command(0x03, bytes([value]))
        await self.coordinator.async_request_refresh()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator._client.send_command(0x04, bytes([int(fan_mode)]))
        await self.coordinator.async_request_refresh()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        # Protocol: 0x01 = start swing, 0x02 = stop swing.
        value = 0x01 if swing_mode == "on" else 0x02
        await self.coordinator._client.send_command(0x10, bytes([value]))
        await self.coordinator.async_request_refresh()
