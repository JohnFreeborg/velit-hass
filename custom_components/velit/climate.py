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
from .packet_utils import celsius_to_fahrenheit, fahrenheit_to_celsius

_LOGGER = logging.getLogger(__name__)

# Heater work mode codes (Query 1 response, work_mode field).
_HEATER_MODE_MANUAL = 1
_HEATER_MODE_THERMOSTAT = 2

# AC operation mode codes (func 0x02).
_AC_MODE_COOL = 1
_AC_MODE_FAN = 3
_AC_MODE_ENERGY_SAVING = 4
_AC_MODE_SLEEP = 5
_AC_MODE_TURBO = 6
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
      HEAT  — heater running (Auto or Manual preset)

    Presets (only meaningful in HEAT mode):
      Auto   — device controls burner level automatically to hit setpoint
      Manual — fixed burner level (gear), thermostat inactive

    Fan modes (gear 1–5, only available in Manual preset):
      Gear selector is hidden in Auto — the device controls level automatically
      and allowing the user to set it there would have no effect.
    """

    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_preset_modes = ["Auto", "Manual"]
    _attr_fan_modes = FAN_MODES
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return supported features, adding FAN_MODE only in Manual preset.

        Gear control is meaningless in Auto — the device picks the level. Hiding
        the selector prevents users from sending a gear command that would be ignored.
        """
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
        )
        if self.preset_mode == "Manual":
            features |= ClimateEntityFeature.FAN_MODE
        return features

    @property
    def min_temp(self) -> float:
        if self.coordinator.temp_unit == UnitOfTemperature.FAHRENHEIT:
            return fahrenheit_to_celsius(40)
        return float(HEATER_MIN_TEMP_C)

    @property
    def max_temp(self) -> float:
        if self.coordinator.temp_unit == UnitOfTemperature.FAHRENHEIT:
            return fahrenheit_to_celsius(99)
        return float(HEATER_MAX_TEMP_C)
    def __init__(
        self,
        coordinator: VelitHeaterCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.data['address']}_climate"
        self._attr_name = entry.data.get(CONF_NAME, entry.data["address"])
        # Optimistic hvac_mode written immediately after a command so the UI
        # reflects the expected state without waiting for the next poll.
        # Cleared once the coordinator confirms the new state, or when the
        # post-command fast poll window expires (command not accepted by device).
        self._optimistic_hvac_mode: HVACMode | None = None
        self._optimistic_preset_mode: str | None = None

    # ------------------------------------------------------------------
    # State properties — read from coordinator data, never from device
    # ------------------------------------------------------------------

    @property
    def device_info(self) -> DeviceInfo:
        # Evaluated on each state write so sw_version is populated as soon as
        # the coordinator queries it on the first poll (initially None).
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.data["address"])},
            name=self._entry.data.get(CONF_NAME, self._entry.data["address"]),
            manufacturer="Velit",
            sw_version=self.coordinator.firmware_version,
        )

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
        if self.coordinator.data["machine_state"] in (0, 2):
            actual = HVACMode.OFF
        else:
            actual = HVACMode.HEAT
        if self._optimistic_hvac_mode is not None:
            if actual == self._optimistic_hvac_mode:
                # Device confirmed expected state — clear optimistic override.
                self._optimistic_hvac_mode = None
            elif self.coordinator._post_command_fast_polls == 0:
                # Fast poll window expired without confirmation — command was not
                # accepted; revert to actual device state.
                self._optimistic_hvac_mode = None
        return self._optimistic_hvac_mode if self._optimistic_hvac_mode is not None else actual

    @property
    def preset_mode(self) -> str | None:
        # Presets only apply in HEAT mode — hide selector in OFF.
        if self.coordinator.data is None:
            return None
        work_mode = self.coordinator.data["work_mode"]
        actual = "Auto" if work_mode == _HEATER_MODE_THERMOSTAT else "Manual"
        if self._optimistic_preset_mode is not None:
            if actual == self._optimistic_preset_mode:
                self._optimistic_preset_mode = None
            elif self.coordinator._post_command_fast_polls == 0:
                self._optimistic_preset_mode = None
        return self._optimistic_preset_mode if self._optimistic_preset_mode is not None else actual

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
        if state == 0:
            return HVACAction.OFF
        if state == 1:
            return HVACAction.HEATING
        if state == 2:
            # Fan running to cool combustion chamber after shutdown.
            return HVACAction.FAN
        # Overtemp standby, cleaning, clean complete — no active output.
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

    @property
    def fan_mode(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return str(self.coordinator.data["current_gear"])

    # ------------------------------------------------------------------
    # Actions — send command then refresh to confirm state
    # ------------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator._client.send_command(0x02, bytes([0x00]))
        elif hvac_mode == HVACMode.HEAT:
            mode_byte = 0x02 if self.preset_mode == "Auto" else 0x01
            await self.coordinator._client.send_command(0x01, bytes([mode_byte]))
        # Write expected state immediately so the UI responds without waiting
        # for the next poll. Coordinator confirmation or window expiry clears it.
        self._optimistic_hvac_mode = hvac_mode
        self.async_write_ha_state()
        self.coordinator._post_command_fast_polls = 6
        await self.coordinator.async_request_refresh()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator._client.send_command(0x07, bytes([int(fan_mode)]))
        self.coordinator._post_command_fast_polls = 6
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        mode_byte = 0x02 if preset_mode == "Auto" else 0x01
        await self.coordinator._client.send_command(0x00, bytes([mode_byte]))
        self._optimistic_preset_mode = preset_mode
        self.async_write_ha_state()
        self.coordinator._post_command_fast_polls = 6
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
        self.coordinator._post_command_fast_polls = 6
        await self.coordinator.async_request_refresh()



class VelitACClimateEntity(CoordinatorEntity[VelitACCoordinator], ClimateEntity):
    """Climate entity for a Velit AC unit (protocol V1.01).

    HVAC modes:
      OFF       — power off (func 0x01, data 0x01)
      COOL      — cooling mode
      FAN_ONLY  — fan mode and vent mode both map here (protocols 0x03 and 0x08)

    Presets (active within the current HVAC mode):
      none           — standard operation
      energy_saving  — protocol mode 0x04
      sleep          — protocol mode 0x05
      turbo          — protocol mode 0x06

    Note: Fan (0x03) and Vent (0x08) are both mapped to FAN_ONLY — the
    functional difference between them is unconfirmed without hardware testing.
    """

    _attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.FAN_ONLY]
    _attr_preset_modes = [AC_PRESET_NONE, AC_PRESET_ENERGY_SAVING, AC_PRESET_SLEEP, AC_PRESET_TURBO]
    _attr_fan_modes = FAN_MODES
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = float(AC_MIN_TEMP_C)
    _attr_max_temp = float(AC_MAX_TEMP_C)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    # Map from AC protocol mode codes to HA HVACMode.
    _MODE_TO_HVAC: dict[int, HVACMode] = {
        _AC_MODE_COOL: HVACMode.COOL,
        _AC_MODE_FAN: HVACMode.FAN_ONLY,
        _AC_MODE_VENT: HVACMode.FAN_ONLY,   # unconfirmed; see class docstring
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
        # Optimistic state — same pattern as VelitHeaterClimateEntity.
        self._optimistic_hvac_mode: HVACMode | None = None
        self._optimistic_preset_mode: str | None = None

    @property
    def hvac_mode(self) -> HVACMode | None:
        if self.coordinator.data is None:
            return None
        # Power state (func 0x01): 0x01 = off, 0x02 = on.
        if self.coordinator.data.get("power") == 0x01:
            actual = HVACMode.OFF
        else:
            mode_code = self.coordinator.data["mode"]
            # Preset codes (4, 5, 6) don't map to an HVACMode directly — return
            # the last known base mode so the UI doesn't flip to an unexpected state.
            if mode_code in self._PRESET_CODES:
                actual = self._last_hvac_mode
            else:
                hvac = self._MODE_TO_HVAC.get(mode_code)
                if hvac is not None:
                    self._last_hvac_mode = hvac
                actual = hvac  # type: ignore[assignment]
        if self._optimistic_hvac_mode is not None:
            if actual == self._optimistic_hvac_mode:
                self._optimistic_hvac_mode = None
            elif self.coordinator._post_command_fast_polls == 0:
                self._optimistic_hvac_mode = None
        return self._optimistic_hvac_mode if self._optimistic_hvac_mode is not None else actual

    @property
    def preset_mode(self) -> str | None:
        if self.coordinator.data is None:
            return None
        mode_code = self.coordinator.data["mode"]
        actual = self._PRESET_CODES.get(mode_code, AC_PRESET_NONE)
        if self._optimistic_preset_mode is not None:
            if actual == self._optimistic_preset_mode:
                self._optimistic_preset_mode = None
            elif self.coordinator._post_command_fast_polls == 0:
                self._optimistic_preset_mode = None
        return self._optimistic_preset_mode if self._optimistic_preset_mode is not None else actual

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
        return str(self.coordinator.data["fan_speed"])

    @property
    def hvac_action(self) -> HVACAction | None:
        """Current action shown on the climate tile.

        Derived from power state and mode — the AC protocol provides no
        dedicated active/idle signal, so this reflects the configured mode
        rather than whether the compressor is actively cycling.
        """
        if self.coordinator.data is None:
            return None
        if self.coordinator.data.get("fault_code", 0) != 0:
            return HVACAction.OFF
        if self.coordinator.data.get("power") == 0x01:
            return HVACAction.OFF
        mode_code = self.coordinator.data["mode"]
        # Preset codes don't carry their own action — resolve from last base mode.
        if mode_code in self._PRESET_CODES:
            base = self._last_hvac_mode
        else:
            base = self._MODE_TO_HVAC.get(mode_code, self._last_hvac_mode)
        if base == HVACMode.COOL:
            return HVACAction.COOLING
        if base == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        return HVACAction.IDLE


    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator._client.send_command(0x01, bytes([0x01]))
        else:
            # Power on first if currently off, then set mode.
            if self.hvac_mode == HVACMode.OFF:
                await self.coordinator._client.send_command(0x01, bytes([0x02]))
            mode_map = {
                HVACMode.COOL: _AC_MODE_COOL,
                HVACMode.FAN_ONLY: _AC_MODE_FAN,
            }
            code = mode_map.get(hvac_mode)
            if code is not None:
                await self.coordinator._client.send_command(0x02, bytes([code]))
        self._optimistic_hvac_mode = hvac_mode
        self.async_write_ha_state()
        self.coordinator._post_command_fast_polls = 6
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == AC_PRESET_NONE:
            # Restore the last base HVAC mode.
            mode_map = {
                HVACMode.COOL: _AC_MODE_COOL,
                HVACMode.FAN_ONLY: _AC_MODE_FAN,
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
        self._optimistic_preset_mode = preset_mode
        self.async_write_ha_state()
        self.coordinator._post_command_fast_polls = 6
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
        self.coordinator._post_command_fast_polls = 6
        await self.coordinator.async_request_refresh()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator._client.send_command(0x04, bytes([int(fan_mode)]))
        self.coordinator._post_command_fast_polls = 6
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Power on and restore the last active HVAC mode."""
        await self.async_set_hvac_mode(self._last_hvac_mode)
