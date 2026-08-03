"""Unit tests for VelitHeaterCoordinator and VelitACCoordinator.

All BLE client calls are mocked — no hardware required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.update_coordinator import UpdateFailed

from unittest.mock import call

from custom_components.velit.coordinator import (
    VelitACCoordinator,
    VelitHeaterCoordinator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(device_type: str = "heater", address: str = "AA:BB:CC:DD:EE:FF"):
    entry = MagicMock()
    entry.data = {"device_type": device_type, "address": address, "name": "Test"}
    entry.options = {}
    return entry


def _make_hass(temp_unit: str = UnitOfTemperature.CELSIUS):
    hass = MagicMock()
    hass.config.units.temperature_unit = temp_unit
    return hass


# Query 1 data payload for a heater in Fahrenheit mode (setpoint = 0x46 = 70°F).
# Layout: [fault][work_mode][gear][set_temp][machine_state][power][pump_freq]
Q1_DATA_F = bytes([0x00, 0x02, 0x05, 0x46, 0x01, 0x50, 0x00])

# Query 1 data for a heater in Celsius mode (setpoint = 0x18 = 24°C).
Q1_DATA_C = bytes([0x00, 0x01, 0x03, 0x18, 0x01, 0x32, 0x05])

# Query 2 data payload.
# Layout: [fault][voltage 2B][fan_rpm 2B][inlet 2B][casing 2B][outlet 2B][alt 2B]
# Using hardware-verified values: voltage=12.7V, inlet=131 (71°F / raw-60), alt=1415.
Q2_DATA = bytes([
    0x00,           # fault
    0x00, 0x7F,     # voltage = 127 → 12.7V
    0x00, 0x00,     # fan RPM = 0
    0x00, 0x83,     # inlet = 131
    0x00, 0x7A,     # casing = 122
    0x00, 0x71,     # outlet = 113
    0x05, 0x87,     # altitude = 1415
])

Q2_DATA_UNAVAILABLE = bytes([
    0x00,
    0x00, 0x7F,
    0x00, 0x00,
    0xFF, 0xFF,     # inlet unavailable
    0x00, 0x7A,
    0xFF, 0xFF,     # outlet unavailable
    0xFF, 0xFF,     # altitude unavailable
])


# ---------------------------------------------------------------------------
# Temperature unit detection
# ---------------------------------------------------------------------------


class TestTempUnitDetection:
    def _coordinator(self, hass_unit=UnitOfTemperature.CELSIUS):
        hass = _make_hass(hass_unit)
        entry = _make_entry()
        with patch(
            "custom_components.velit.coordinator.VelitHeaterClient"
        ):
            coord = VelitHeaterCoordinator(hass, entry)
        return coord

    def test_celsius_range_detected(self):
        coord = self._coordinator()
        assert coord._detect_temp_unit(16) == UnitOfTemperature.CELSIUS
        assert coord._detect_temp_unit(24) == UnitOfTemperature.CELSIUS
        assert coord._detect_temp_unit(30) == UnitOfTemperature.CELSIUS

    def test_fahrenheit_range_detected(self):
        coord = self._coordinator()
        assert coord._detect_temp_unit(61) == UnitOfTemperature.FAHRENHEIT
        assert coord._detect_temp_unit(70) == UnitOfTemperature.FAHRENHEIT
        assert coord._detect_temp_unit(86) == UnitOfTemperature.FAHRENHEIT

    def test_ambiguous_falls_back_to_ha_celsius(self):
        coord = self._coordinator(hass_unit=UnitOfTemperature.CELSIUS)
        assert coord._detect_temp_unit(0) == UnitOfTemperature.CELSIUS
        assert coord._detect_temp_unit(50) == UnitOfTemperature.CELSIUS

    def test_ambiguous_falls_back_to_ha_fahrenheit(self):
        coord = self._coordinator(hass_unit=UnitOfTemperature.FAHRENHEIT)
        assert coord._detect_temp_unit(0) == UnitOfTemperature.FAHRENHEIT

    def test_boundary_values_are_ambiguous(self):
        # 31 and 60 are outside both named ranges — they fall back to HA system unit.
        # With a Celsius HA system, both should return Celsius (the fallback), not Fahrenheit.
        coord = self._coordinator(hass_unit=UnitOfTemperature.CELSIUS)
        assert coord._detect_temp_unit(31) == UnitOfTemperature.CELSIUS   # fallback
        assert coord._detect_temp_unit(60) == UnitOfTemperature.CELSIUS   # fallback

    def test_boundary_values_with_fahrenheit_ha_preference(self):
        # Same ambiguous values with a Fahrenheit HA system should return Fahrenheit.
        coord = self._coordinator(hass_unit=UnitOfTemperature.FAHRENHEIT)
        assert coord._detect_temp_unit(31) == UnitOfTemperature.FAHRENHEIT
        assert coord._detect_temp_unit(60) == UnitOfTemperature.FAHRENHEIT


# ---------------------------------------------------------------------------
# Heater coordinator — parse logic
# ---------------------------------------------------------------------------


class TestVelitHeaterCoordinatorParse:
    def _make_coord(self, hass_unit=UnitOfTemperature.CELSIUS):
        hass = _make_hass(hass_unit)
        entry = _make_entry()
        with patch("custom_components.velit.coordinator.VelitHeaterClient"):
            coord = VelitHeaterCoordinator(hass, entry)
        return coord

    def test_fahrenheit_setpoint_detected_and_converted(self):
        coord = self._make_coord()
        data = coord._parse(Q1_DATA_F, Q2_DATA)
        assert coord.temp_unit == UnitOfTemperature.FAHRENHEIT
        # 70°F → (70-32)/1.8 ≈ 21.1°C
        assert abs(data["set_temp_c"] - 21.11) < 0.1

    def test_celsius_setpoint_detected(self):
        coord = self._make_coord()
        data = coord._parse(Q1_DATA_C, Q2_DATA)
        assert coord.temp_unit == UnitOfTemperature.CELSIUS
        assert data["set_temp_c"] == 24.0

    def test_fault_code_mapped_to_name(self):
        coord = self._make_coord()
        data = coord._parse(Q1_DATA_F, Q2_DATA)
        assert data["fault_code"] == 0
        assert data["fault_name"] == "No Fault"

    def test_fault_code_14_mapped_correctly(self):
        coord = self._make_coord()
        q1_fault = bytes([0x0E]) + Q1_DATA_F[1:]  # inject fault code 14
        data = coord._parse(q1_fault, Q2_DATA)
        assert data["fault_name"] == "CO Pollution Exceeded"

    def test_machine_state_mapped(self):
        coord = self._make_coord()
        data = coord._parse(Q1_DATA_F, Q2_DATA)
        assert data["machine_state"] == 1
        assert data["machine_state_str"] == "Normal"

    def test_voltage_decoded(self):
        coord = self._make_coord()
        data = coord._parse(Q1_DATA_F, Q2_DATA)
        assert data["voltage_v"] == pytest.approx(12.7)

    def test_inlet_temp_decoded_fahrenheit(self):
        """Hardware-verified: inlet raw 131, in F mode → 131-60=71°F → 21.7°C."""
        coord = self._make_coord()
        data = coord._parse(Q1_DATA_F, Q2_DATA)
        assert coord.temp_unit == UnitOfTemperature.FAHRENHEIT
        # raw 131, offset -60 = 71°F, converted to C ≈ 21.67
        assert abs(data["inlet_temp_c"] - 21.67) < 0.1

    def test_sensor_unavailable_returns_none(self):
        coord = self._make_coord()
        data = coord._parse(Q1_DATA_F, Q2_DATA_UNAVAILABLE)
        assert data["inlet_temp_c"] is None
        assert data["outlet_temp_c"] is None
        assert data["altitude"] is None

    def test_gear_and_work_mode(self):
        coord = self._make_coord()
        data = coord._parse(Q1_DATA_F, Q2_DATA)
        assert data["current_gear"] == 5
        assert data["work_mode"] == 2  # thermostat

    def test_fuel_pump_frequency(self):
        coord = self._make_coord()
        data = coord._parse(Q1_DATA_C, Q2_DATA)
        # Q1_DATA_C has pump_freq byte = 0x05 → 0.5 Hz
        assert data["fuel_pump_hz"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Heater coordinator — poll raises UpdateFailed on no response
# ---------------------------------------------------------------------------


class TestVelitHeaterCoordinatorPoll:
    async def _make_coord(self):
        hass = _make_hass()
        entry = _make_entry()
        with patch("custom_components.velit.coordinator.VelitHeaterClient") as mock_cls:
            coord = VelitHeaterCoordinator(hass, entry)
            coord._client = mock_cls.return_value
        return coord

    async def test_raises_on_q1_none(self):
        coord = await self._make_coord()
        coord._client.send_command = AsyncMock(return_value=None)
        with pytest.raises(UpdateFailed):
            await coord._async_poll()

    async def test_raises_on_q2_none(self):
        coord = await self._make_coord()
        q1_response = {"data": Q1_DATA_F, "func": 0x0A}
        coord._client.send_command = AsyncMock(
            side_effect=[q1_response, None]
        )
        with pytest.raises(UpdateFailed):
            await coord._async_poll()

    async def test_returns_data_on_success(self):
        coord = await self._make_coord()
        q1_response = {"data": Q1_DATA_F, "func": 0x0A}
        q2_response = {"data": Q2_DATA, "func": 0x0B}
        coord._client.send_command = AsyncMock(
            side_effect=[q1_response, q2_response]
        )
        data = await coord._async_poll()
        assert data["fault_code"] == 0
        assert data["voltage_v"] == pytest.approx(12.7)


# ---------------------------------------------------------------------------
# AC coordinator — basic poll
# ---------------------------------------------------------------------------


class TestVelitACCoordinatorPoll:
    async def _make_coord(self):
        hass = _make_hass()
        entry = _make_entry(device_type="ac")
        with patch("custom_components.velit.coordinator.VelitACClient") as mock_cls:
            coord = VelitACCoordinator(hass, entry)
            coord._client = mock_cls.return_value
        return coord

    def _mock_responses(self, mode=1, temp=24, fan=3, swing=2):
        """Build the ordered list of send_command responses for a full AC poll."""
        return [
            {"data": bytes([mode]), "func": 0x02},   # mode
            {"data": bytes([temp]), "func": 0x03},   # temp
            {"data": bytes([fan]),  "func": 0x04},   # fan
            {"data": bytes([swing]),"func": 0x10},   # swing
            None,                                     # inlet (unconfirmed format)
            None,                                     # fault (unconfirmed format)
        ]

    async def test_raises_on_mode_none(self):
        coord = await self._make_coord()
        coord._client.send_command = AsyncMock(return_value=None)
        with pytest.raises(UpdateFailed):
            await coord._async_poll()

    async def test_returns_data_on_success(self):
        coord = await self._make_coord()
        coord._client.send_command = AsyncMock(
            side_effect=self._mock_responses(mode=1, temp=24, fan=3, swing=2)
        )
        data = await coord._async_poll()
        assert data["mode"] == 1
        assert data["set_temp_c"] == 24.0
        assert data["fan_speed"] == 3
        assert data["swing"] == 2
        assert data["inlet_temp_raw"] is None
        assert data["fault_raw"] is None

    async def test_unit_detected_celsius(self):
        coord = await self._make_coord()
        coord._client.send_command = AsyncMock(
            side_effect=self._mock_responses(temp=24)
        )
        await coord._async_poll()
        assert coord.temp_unit == UnitOfTemperature.CELSIUS

    async def test_unit_detected_fahrenheit(self):
        coord = await self._make_coord()
        coord._client.send_command = AsyncMock(
            side_effect=self._mock_responses(temp=75)
        )
        await coord._async_poll()
        assert coord.temp_unit == UnitOfTemperature.FAHRENHEIT


# ---------------------------------------------------------------------------
# Fault issue registry
# ---------------------------------------------------------------------------


class TestFaultIssueRegistry:
    def _make_coord(self):
        hass = _make_hass()
        entry = _make_entry()
        with patch("custom_components.velit.coordinator.VelitHeaterClient"):
            coord = VelitHeaterCoordinator(hass, entry)
        return coord

    def test_creates_issue_when_fault_active(self):
        coord = self._make_coord()
        data = {"fault_code": 1, "fault_name": "Ignition Failure"}
        with patch(
            "custom_components.velit.coordinator.ir.async_create_issue"
        ) as mock_create, patch(
            "custom_components.velit.coordinator.ir.async_delete_issue"
        ):
            coord._update_fault_issue(data)
            mock_create.assert_called_once()
            _, args = mock_create.call_args[0], mock_create.call_args
            assert args[0][1] == "velit"  # domain
            assert "fault_" in args[0][2]  # issue_id

    def test_deletes_issue_when_fault_clears(self):
        coord = self._make_coord()
        data = {"fault_code": 0, "fault_name": "No Fault"}
        with patch(
            "custom_components.velit.coordinator.ir.async_delete_issue"
        ) as mock_delete, patch(
            "custom_components.velit.coordinator.ir.async_create_issue"
        ):
            coord._update_fault_issue(data)
            mock_delete.assert_called_once()

    def test_no_issue_created_when_no_fault(self):
        coord = self._make_coord()
        data = {"fault_code": 0, "fault_name": "No Fault"}
        with patch(
            "custom_components.velit.coordinator.ir.async_create_issue"
        ) as mock_create, patch(
            "custom_components.velit.coordinator.ir.async_delete_issue"
        ):
            coord._update_fault_issue(data)
            mock_create.assert_not_called()

    def test_issue_placeholders_include_fault_name(self):
        coord = self._make_coord()
        data = {"fault_code": 7, "fault_name": "Fuel Pump Fault"}
        with patch(
            "custom_components.velit.coordinator.ir.async_create_issue"
        ) as mock_create, patch(
            "custom_components.velit.coordinator.ir.async_delete_issue"
        ):
            coord._update_fault_issue(data)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["fault_name"] == "Fuel Pump Fault"
            assert placeholders["fault_code"] == "7"
            assert placeholders["fault_code_display"] == "E07"

    def test_issue_fault_code_display_zero_padded(self):
        coord = self._make_coord()
        data = {"fault_code": 1, "fault_name": "Ignition Failure"}
        with patch(
            "custom_components.velit.coordinator.ir.async_create_issue"
        ) as mock_create, patch(
            "custom_components.velit.coordinator.ir.async_delete_issue"
        ):
            coord._update_fault_issue(data)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["fault_code_display"] == "E01"
