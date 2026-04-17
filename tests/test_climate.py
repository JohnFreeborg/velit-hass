"""Unit tests for VelitHeaterClimateEntity and VelitACClimateEntity.

Coordinator is mocked — tests verify that the correct commands are sent
for each action and that state properties read correctly from coordinator data.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import UnitOfTemperature

from custom_components.velit.climate import (
    VelitACClimateEntity,
    VelitHeaterClimateEntity,
    AC_PRESET_ENERGY_SAVING,
    AC_PRESET_NONE,
    AC_PRESET_SLEEP,
    AC_PRESET_TURBO,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(device_type="heater", address="AA:BB:CC:DD:EE:FF", options=None):
    entry = MagicMock()
    entry.data = {"device_type": device_type, "address": address, "name": "Test Heater"}
    entry.options = options if options is not None else {}
    return entry


_UNSET = object()


def _make_heater_coord(data=_UNSET, temp_unit=UnitOfTemperature.CELSIUS):
    coord = MagicMock()
    coord.data = data if data is not _UNSET else {
        "fault_code": 0,
        "fault_name": "No Fault",
        "work_mode": 1,
        "current_gear": 3,
        "set_temp_c": 22.0,
        "machine_state": 1,
        "machine_state_str": "Normal",
        "heater_power_w": 80,
        "fuel_pump_hz": 0.5,
        "voltage_v": 12.7,
        "fan_rpm": 0,
        "inlet_temp_c": 21.7,
        "casing_temp_c": 16.7,
        "outlet_temp_c": 11.7,
        "altitude": 1415,
    }
    coord.temp_unit = temp_unit
    coord.async_request_refresh = AsyncMock()
    coord._client = MagicMock()
    coord._client.send_command = AsyncMock()
    coord._post_command_fast_polls = 0
    return coord


def _make_ac_coord(data=_UNSET, temp_unit=UnitOfTemperature.CELSIUS):
    coord = MagicMock()
    coord.data = data if data is not _UNSET else {
        "power": 0x02,
        "mode": 1,
        "set_temp_c": 22.0,
        "fan_speed": 3,
        "swing": 2,
        "inlet_temp_c": None,
        "fault_code": 0,
        "fault_name": "No Fault",
    }
    coord.temp_unit = temp_unit
    coord.async_request_refresh = AsyncMock()
    coord._client = MagicMock()
    coord._client.send_command = AsyncMock()
    coord._post_command_fast_polls = 0
    return coord


def _heater_entity(data=_UNSET, temp_unit=UnitOfTemperature.CELSIUS, options=None):
    coord = _make_heater_coord(data, temp_unit)
    entry = _make_entry(options=options)
    entity = VelitHeaterClimateEntity.__new__(VelitHeaterClimateEntity)
    entity.coordinator = coord
    entity._entry = entry
    entity._attr_unique_id = "test_climate"
    entity._attr_name = "Test Heater"
    entity._attr_device_info = MagicMock()
    entity._optimistic_hvac_mode = None
    entity._optimistic_preset_mode = None
    entity._ventilating = False
    entity.async_write_ha_state = MagicMock()
    return entity, coord


def _ac_entity(data=_UNSET, temp_unit=UnitOfTemperature.CELSIUS):
    coord = _make_ac_coord(data, temp_unit)
    entry = _make_entry(device_type="ac")
    entity = VelitACClimateEntity.__new__(VelitACClimateEntity)
    entity.coordinator = coord
    entity._entry = entry
    entity._attr_unique_id = "test_climate_ac"
    entity._attr_name = "Test AC"
    entity._attr_device_info = MagicMock()
    entity._last_hvac_mode = HVACMode.COOL
    entity._optimistic_hvac_mode = None
    entity._optimistic_preset_mode = None
    entity.async_write_ha_state = MagicMock()
    return entity, coord


# ---------------------------------------------------------------------------
# Heater — state properties
# ---------------------------------------------------------------------------


class TestHeaterClimateState:
    def test_hvac_mode_off_when_standby(self):
        entity, _ = _heater_entity(data={**_make_heater_coord().data, "machine_state": 0})
        assert entity.hvac_mode == HVACMode.OFF

    def test_hvac_mode_heat_when_running(self):
        entity, _ = _heater_entity()
        assert entity.hvac_mode == HVACMode.HEAT

    def test_preset_manual(self):
        entity, _ = _heater_entity()
        assert entity.preset_mode == "Manual"

    def test_preset_auto(self):
        data = {**_make_heater_coord().data, "work_mode": 2}
        entity, _ = _heater_entity(data=data)
        assert entity.preset_mode == "Auto"

    def test_current_temperature(self):
        entity, _ = _heater_entity()
        assert entity.current_temperature == pytest.approx(21.7)

    def test_target_temperature(self):
        entity, _ = _heater_entity()
        assert entity.target_temperature == pytest.approx(22.0)

    def test_hvac_mode_fan_only_when_ventilating(self):
        entity, _ = _heater_entity()
        entity._ventilating = True
        assert entity.hvac_mode == HVACMode.FAN_ONLY

    def test_preset_none_when_ventilating(self):
        entity, _ = _heater_entity()
        entity._ventilating = True
        assert entity.preset_mode is None

    def test_hvac_action_fan_when_ventilating(self):
        entity, _ = _heater_entity()
        entity._ventilating = True
        assert entity.hvac_action == HVACAction.FAN

    def test_hvac_action_heating_when_normal(self):
        data = {**_make_heater_coord().data, "machine_state": 1, "fault_code": 0}
        entity, _ = _heater_entity(data=data)
        assert entity.hvac_action == HVACAction.HEATING

    def test_hvac_action_fan_when_cooling_down(self):
        data = {**_make_heater_coord().data, "machine_state": 2, "fault_code": 0}
        entity, _ = _heater_entity(data=data)
        assert entity.hvac_action == HVACAction.FAN

    def test_hvac_action_off_when_standby(self):
        data = {**_make_heater_coord().data, "machine_state": 0, "fault_code": 0}
        entity, _ = _heater_entity(data=data)
        assert entity.hvac_action == HVACAction.OFF

    def test_hvac_action_off_when_fault_active(self):
        data = {**_make_heater_coord().data, "machine_state": 0, "fault_code": 1}
        entity, _ = _heater_entity(data=data)
        assert entity.hvac_action == HVACAction.OFF

    def test_hvac_action_none_when_no_data(self):
        entity, _ = _heater_entity(data=None)
        assert entity.hvac_action is None

    def test_extra_state_attributes_contains_machine_state_and_fault(self):
        entity, _ = _heater_entity()
        attrs = entity.extra_state_attributes
        assert attrs["machine_state"] == "Normal"
        assert attrs["fault"] == "No Fault"

    def test_extra_state_attributes_empty_when_no_data(self):
        entity, _ = _heater_entity(data=None)
        assert entity.extra_state_attributes == {}

    def test_none_data_returns_none(self):
        entity, _ = _heater_entity(data=None)
        assert entity.hvac_mode is None
        assert entity.current_temperature is None
        assert entity.target_temperature is None


# ---------------------------------------------------------------------------
# Heater — actions
# ---------------------------------------------------------------------------


class TestHeaterClimateActions:
    async def test_set_hvac_off(self):
        entity, coord = _heater_entity()
        await entity.async_set_hvac_mode(HVACMode.OFF)
        coord._client.send_command.assert_called_once_with(0x02, bytes([0x00]))
        coord.async_request_refresh.assert_called_once()

    async def test_set_hvac_heat_manual(self):
        entity, coord = _heater_entity()
        await entity.async_set_hvac_mode(HVACMode.HEAT)
        coord._client.send_command.assert_called_once_with(0x01, bytes([0x01]))

    async def test_set_hvac_heat_auto(self):
        data = {**_make_heater_coord().data, "work_mode": 2}
        entity, coord = _heater_entity(data=data)
        await entity.async_set_hvac_mode(HVACMode.HEAT)
        coord._client.send_command.assert_called_once_with(0x01, bytes([0x02]))

    async def test_set_hvac_fan_only(self):
        entity, coord = _heater_entity()
        await entity.async_set_hvac_mode(HVACMode.FAN_ONLY)
        coord._client.send_command.assert_called_once_with(0x03, bytes([0x00]))
        assert entity._ventilating is True

    async def test_set_hvac_off_clears_ventilating(self):
        entity, coord = _heater_entity()
        entity._ventilating = True
        await entity.async_set_hvac_mode(HVACMode.OFF)
        assert entity._ventilating is False

    async def test_set_hvac_heat_from_fan_only_stops_ventilation_first(self):
        entity, coord = _heater_entity()
        entity._ventilating = True
        await entity.async_set_hvac_mode(HVACMode.HEAT)
        calls = coord._client.send_command.call_args_list
        assert calls[0] == ((0x04, bytes([0x00])),)
        assert calls[1][0][0] == 0x01
        assert entity._ventilating is False

    async def test_set_preset_auto(self):
        entity, coord = _heater_entity()
        await entity.async_set_preset_mode("Auto")
        coord._client.send_command.assert_called_once_with(0x00, bytes([0x02]))

    async def test_set_preset_manual(self):
        entity, coord = _heater_entity()
        await entity.async_set_preset_mode("Manual")
        coord._client.send_command.assert_called_once_with(0x00, bytes([0x01]))

    async def test_set_temperature_celsius(self):
        entity, coord = _heater_entity(temp_unit=UnitOfTemperature.CELSIUS)
        await entity.async_set_temperature(temperature=24.0)
        coord._client.send_command.assert_called_once_with(0x08, bytes([24]))

    async def test_set_temperature_converts_to_fahrenheit_for_f_device(self):
        """Device is in °F mode — we must send °F to avoid flipping the LCD unit."""
        entity, coord = _heater_entity(temp_unit=UnitOfTemperature.FAHRENHEIT)
        # 24°C → 75.2°F → rounds to 75
        await entity.async_set_temperature(temperature=24.0)
        coord._client.send_command.assert_called_once_with(0x08, bytes([75]))

    async def test_refresh_called_after_each_action(self):
        entity, coord = _heater_entity()
        await entity.async_set_hvac_mode(HVACMode.FAN_ONLY)
        coord.async_request_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# AC — state properties
# ---------------------------------------------------------------------------


class TestACClimateState:
    def test_hvac_mode_cool(self):
        entity, _ = _ac_entity()
        assert entity.hvac_mode == HVACMode.COOL

    def test_hvac_mode_off(self):
        data = {**_make_ac_coord().data, "power": 0x01}
        entity, _ = _ac_entity(data=data)
        assert entity.hvac_mode == HVACMode.OFF

    def test_hvac_mode_dry(self):
        data = {**_make_ac_coord().data, "mode": 7}
        entity, _ = _ac_entity(data=data)
        assert entity.hvac_mode == HVACMode.DRY

    def test_preset_none_for_base_modes(self):
        entity, _ = _ac_entity()
        assert entity.preset_mode == AC_PRESET_NONE

    def test_preset_energy_saving(self):
        data = {**_make_ac_coord().data, "mode": 4}
        entity, _ = _ac_entity(data=data)
        assert entity.preset_mode == AC_PRESET_ENERGY_SAVING

    def test_preset_sleep(self):
        data = {**_make_ac_coord().data, "mode": 5}
        entity, _ = _ac_entity(data=data)
        assert entity.preset_mode == AC_PRESET_SLEEP

    def test_preset_turbo(self):
        data = {**_make_ac_coord().data, "mode": 6}
        entity, _ = _ac_entity(data=data)
        assert entity.preset_mode == AC_PRESET_TURBO

    def test_swing_on(self):
        data = {**_make_ac_coord().data, "swing": 1}
        entity, _ = _ac_entity(data=data)
        assert entity.swing_mode == "on"

    def test_swing_off(self):
        entity, _ = _ac_entity()
        assert entity.swing_mode == "off"

    def test_fan_mode(self):
        entity, _ = _ac_entity()
        assert entity.fan_mode == "3"

    def test_hvac_mode_heat(self):
        data = {**_make_ac_coord().data, "mode": 2}
        entity, _ = _ac_entity(data=data)
        assert entity.hvac_mode == HVACMode.HEAT

    def test_hvac_mode_fan_only(self):
        data = {**_make_ac_coord().data, "mode": 3}
        entity, _ = _ac_entity(data=data)
        assert entity.hvac_mode == HVACMode.FAN_ONLY

    def test_hvac_mode_none_when_no_data(self):
        entity, _ = _ac_entity(data=None)
        assert entity.hvac_mode is None

    def test_preset_mode_none_when_no_data(self):
        entity, _ = _ac_entity(data=None)
        assert entity.preset_mode is None

    def test_current_temperature_from_inlet(self):
        data = {**_make_ac_coord().data, "inlet_temp_c": 24.0}
        entity, _ = _ac_entity(data=data)
        assert entity.current_temperature == 24.0

    def test_current_temperature_none_when_no_data(self):
        entity, _ = _ac_entity(data=None)
        assert entity.current_temperature is None

    def test_target_temperature_from_coord(self):
        entity, _ = _ac_entity()
        assert entity.target_temperature == 22.0

    def test_target_temperature_none_when_no_data(self):
        entity, _ = _ac_entity(data=None)
        assert entity.target_temperature is None


# ---------------------------------------------------------------------------
# AC — optimistic state
# ---------------------------------------------------------------------------


class TestACClimateOptimistic:
    def test_optimistic_hvac_mode_returned_immediately(self):
        entity, coord = _ac_entity()
        # Set optimistic before coordinator confirms.
        entity._optimistic_hvac_mode = HVACMode.HEAT
        coord._post_command_fast_polls = 3
        assert entity.hvac_mode == HVACMode.HEAT

    def test_optimistic_hvac_mode_clears_when_confirmed(self):
        # Coordinator data already shows HEAT — optimistic agrees, so it clears.
        data = {**_make_ac_coord().data, "mode": 2}
        entity, coord = _ac_entity(data=data)
        entity._optimistic_hvac_mode = HVACMode.HEAT
        coord._post_command_fast_polls = 3
        result = entity.hvac_mode
        assert result == HVACMode.HEAT
        assert entity._optimistic_hvac_mode is None

    def test_optimistic_hvac_mode_clears_when_fast_polls_exhausted(self):
        # Fast poll window expired without confirmation — revert to actual.
        entity, coord = _ac_entity()  # actual = COOL (mode=1)
        entity._optimistic_hvac_mode = HVACMode.HEAT
        coord._post_command_fast_polls = 0
        result = entity.hvac_mode
        assert result == HVACMode.COOL
        assert entity._optimistic_hvac_mode is None

    def test_optimistic_preset_mode_returned_immediately(self):
        entity, coord = _ac_entity()
        entity._optimistic_preset_mode = AC_PRESET_TURBO
        coord._post_command_fast_polls = 3
        assert entity.preset_mode == AC_PRESET_TURBO

    def test_optimistic_preset_mode_clears_when_confirmed(self):
        # Coordinator data shows turbo (mode=6) — matches optimistic, so it clears.
        data = {**_make_ac_coord().data, "mode": 6}
        entity, coord = _ac_entity(data=data)
        entity._optimistic_preset_mode = AC_PRESET_TURBO
        coord._post_command_fast_polls = 3
        result = entity.preset_mode
        assert result == AC_PRESET_TURBO
        assert entity._optimistic_preset_mode is None


# ---------------------------------------------------------------------------
# AC — actions
# ---------------------------------------------------------------------------


class TestACClimateActions:
    async def test_set_hvac_off(self):
        entity, coord = _ac_entity()
        await entity.async_set_hvac_mode(HVACMode.OFF)
        coord._client.send_command.assert_called_once_with(0x01, bytes([0x01]))

    async def test_set_hvac_cool(self):
        entity, coord = _ac_entity(data={**_make_ac_coord().data, "power": 0x01})
        await entity.async_set_hvac_mode(HVACMode.COOL)
        # Powers on first, then sets mode.
        calls = coord._client.send_command.call_args_list
        assert calls[0].args == (0x01, bytes([0x02]))
        assert calls[1].args == (0x02, bytes([0x01]))

    async def test_set_hvac_dry(self):
        entity, coord = _ac_entity()
        await entity.async_set_hvac_mode(HVACMode.DRY)
        coord._client.send_command.assert_called_once_with(0x02, bytes([0x07]))

    async def test_set_preset_energy_saving(self):
        entity, coord = _ac_entity()
        await entity.async_set_preset_mode(AC_PRESET_ENERGY_SAVING)
        coord._client.send_command.assert_called_once_with(0x02, bytes([0x04]))

    async def test_set_preset_none_restores_last_mode(self):
        entity, coord = _ac_entity()
        entity._last_hvac_mode = HVACMode.HEAT
        await entity.async_set_preset_mode(AC_PRESET_NONE)
        coord._client.send_command.assert_called_once_with(0x02, bytes([0x02]))

    async def test_set_temperature_celsius(self):
        entity, coord = _ac_entity(temp_unit=UnitOfTemperature.CELSIUS)
        await entity.async_set_temperature(temperature=25.0)
        coord._client.send_command.assert_called_once_with(0x03, bytes([25]))

    async def test_set_temperature_fahrenheit_device(self):
        entity, coord = _ac_entity(temp_unit=UnitOfTemperature.FAHRENHEIT)
        # 25°C → 77°F
        await entity.async_set_temperature(temperature=25.0)
        coord._client.send_command.assert_called_once_with(0x03, bytes([77]))

    async def test_set_fan_mode(self):
        entity, coord = _ac_entity()
        await entity.async_set_fan_mode("4")
        coord._client.send_command.assert_called_once_with(0x04, bytes([4]))

    async def test_set_swing_on(self):
        entity, coord = _ac_entity()
        await entity.async_set_swing_mode("on")
        coord._client.send_command.assert_called_once_with(0x10, bytes([0x01]))

    async def test_set_swing_off(self):
        entity, coord = _ac_entity()
        await entity.async_set_swing_mode("off")
        coord._client.send_command.assert_called_once_with(0x10, bytes([0x02]))

    async def test_refresh_called_after_action(self):
        entity, coord = _ac_entity()
        await entity.async_set_fan_mode("1")
        coord.async_request_refresh.assert_called_once()

    async def test_write_ha_state_called_after_hvac_set(self):
        entity, coord = _ac_entity()
        await entity.async_set_hvac_mode(HVACMode.OFF)
        entity.async_write_ha_state.assert_called_once()

    async def test_write_ha_state_called_after_preset_set(self):
        entity, coord = _ac_entity()
        await entity.async_set_preset_mode(AC_PRESET_SLEEP)
        entity.async_write_ha_state.assert_called_once()

    async def test_post_command_fast_polls_armed_after_temperature(self):
        entity, coord = _ac_entity()
        await entity.async_set_temperature(temperature=25.0)
        assert coord._post_command_fast_polls == 6

    async def test_post_command_fast_polls_armed_after_fan(self):
        entity, coord = _ac_entity()
        await entity.async_set_fan_mode("2")
        assert coord._post_command_fast_polls == 6

    async def test_post_command_fast_polls_armed_after_swing(self):
        entity, coord = _ac_entity()
        await entity.async_set_swing_mode("on")
        assert coord._post_command_fast_polls == 6

    async def test_turn_on_restores_last_hvac_mode(self):
        # Device is off; async_turn_on should power on and restore last mode.
        entity, coord = _ac_entity(data={**_make_ac_coord().data, "power": 0x01})
        entity._last_hvac_mode = HVACMode.HEAT
        await entity.async_turn_on()
        calls = coord._client.send_command.call_args_list
        assert calls[0].args == (0x01, bytes([0x02]))   # power on
        assert calls[1].args == (0x02, bytes([0x02]))   # HEAT = 0x02


# ---------------------------------------------------------------------------
# Heater — icon
# ---------------------------------------------------------------------------


class TestHeaterClimateIcon:
    def test_icon_alert_when_fault_active(self):
        data = {**_make_heater_coord().data, "fault_code": 1}
        entity, _ = _heater_entity(data=data)
        assert entity.icon == "mdi:alert-circle"

    def test_icon_thermostat_when_no_fault(self):
        entity, _ = _heater_entity()
        assert entity.icon == "mdi:thermostat"

    def test_icon_thermostat_when_no_data(self):
        entity, _ = _heater_entity(data=None)
        assert entity.icon == "mdi:thermostat"


# ---------------------------------------------------------------------------
# Heater — available (unavailable_on_fault option)
# ---------------------------------------------------------------------------


class TestHeaterClimateAvailable:
    def test_available_by_default_even_with_fault(self):
        """Option defaults off — entity stays available on fault."""
        data = {**_make_heater_coord().data, "fault_code": 1}
        entity, _ = _heater_entity(data=data)
        assert entity.available is True

    def test_unavailable_when_option_enabled_and_fault_active(self):
        from custom_components.velit.const import CONF_UNAVAILABLE_ON_FAULT
        data = {**_make_heater_coord().data, "fault_code": 3}
        entity, _ = _heater_entity(data=data, options={CONF_UNAVAILABLE_ON_FAULT: True})
        assert entity.available is False

    def test_available_when_option_enabled_but_no_fault(self):
        from custom_components.velit.const import CONF_UNAVAILABLE_ON_FAULT
        entity, _ = _heater_entity(options={CONF_UNAVAILABLE_ON_FAULT: True})
        assert entity.available is True

    def test_available_when_option_enabled_but_no_data(self):
        from custom_components.velit.const import CONF_UNAVAILABLE_ON_FAULT
        entity, _ = _heater_entity(data=None, options={CONF_UNAVAILABLE_ON_FAULT: True})
        assert entity.available is True
