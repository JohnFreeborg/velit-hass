"""Unit tests for Velit sensor entities.

All coordinator access is mocked — no hardware or BLE required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.const import UnitOfTemperature

from custom_components.velit.sensor import (
    VelitACFaultSensorEntity,
    VelitHeaterSensorEntity,
    HEATER_SENSORS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_heater_data(**overrides):
    base = {
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
        "fan_rpm": 1200,
        "inlet_temp_c": 21.7,
        "casing_temp_c": 16.7,
        "outlet_temp_c": 11.7,
        "altitude": 1415,
    }
    base.update(overrides)
    return base


_UNSET = object()


def _sensor_for_key(key: str, data=_UNSET, temp_unit=UnitOfTemperature.CELSIUS):
    description = next(d for d in HEATER_SENSORS if d.key == key)
    coord = MagicMock()
    coord.data = data if data is not _UNSET else _make_heater_data()
    coord.temp_unit = temp_unit
    entry = MagicMock()
    entry.data = {"address": "AA:BB:CC:DD:EE:FF", "name": "Test"}
    entity = VelitHeaterSensorEntity.__new__(VelitHeaterSensorEntity)
    entity.coordinator = coord
    entity.entity_description = description
    entity._attr_unique_id = f"test_{key}"
    entity._attr_device_info = MagicMock()
    if key == "altitude":
        entity._attr_native_unit_of_measurement = (
            "ft" if temp_unit == UnitOfTemperature.FAHRENHEIT else "m"
        )
    return entity


# ---------------------------------------------------------------------------
# Heater sensor values
# ---------------------------------------------------------------------------


class TestHeaterSensorValues:
    def test_inlet_temp(self):
        entity = _sensor_for_key("inlet_temp")
        assert entity.native_value == pytest.approx(21.7)

    def test_casing_temp(self):
        entity = _sensor_for_key("casing_temp")
        assert entity.native_value == pytest.approx(16.7)

    def test_outlet_temp(self):
        entity = _sensor_for_key("outlet_temp")
        assert entity.native_value == pytest.approx(11.7)

    def test_voltage(self):
        entity = _sensor_for_key("voltage")
        assert entity.native_value == pytest.approx(12.7)

    def test_fan_rpm(self):
        entity = _sensor_for_key("fan_rpm")
        assert entity.native_value == 1200

    def test_altitude(self):
        entity = _sensor_for_key("altitude")
        assert entity.native_value == 1415

    def test_fault_no_fault(self):
        entity = _sensor_for_key("fault_code")
        assert entity.native_value == "No Fault"

    def test_fault_co_pollution(self):
        data = _make_heater_data(fault_name="CO Pollution Exceeded")
        entity = _sensor_for_key("fault_code", data=data)
        assert entity.native_value == "CO Pollution Exceeded"

    def test_machine_state(self):
        entity = _sensor_for_key("machine_state")
        assert entity.native_value == "Normal"

    def test_machine_state_cooling_down(self):
        data = _make_heater_data(machine_state_str="Cooling Down")
        entity = _sensor_for_key("machine_state", data=data)
        assert entity.native_value == "Cooling Down"


# ---------------------------------------------------------------------------
# Unavailable sensor values
# ---------------------------------------------------------------------------


class TestHeaterSensorUnavailable:
    def test_inlet_temp_none_when_unavailable(self):
        data = _make_heater_data(inlet_temp_c=None)
        entity = _sensor_for_key("inlet_temp", data=data)
        assert entity.native_value is None

    def test_outlet_temp_none_when_unavailable(self):
        data = _make_heater_data(outlet_temp_c=None)
        entity = _sensor_for_key("outlet_temp", data=data)
        assert entity.native_value is None

    def test_altitude_none_when_unavailable(self):
        data = _make_heater_data(altitude=None)
        entity = _sensor_for_key("altitude", data=data)
        assert entity.native_value is None

    def test_all_none_when_coordinator_data_none(self):
        for description in HEATER_SENSORS:
            entity = _sensor_for_key(description.key, data=None)
            assert entity.native_value is None


# ---------------------------------------------------------------------------
# Altitude unit
# ---------------------------------------------------------------------------


class TestAltitudeUnit:
    def test_altitude_unit_metres_in_celsius_mode(self):
        entity = _sensor_for_key("altitude", temp_unit=UnitOfTemperature.CELSIUS)
        assert entity._attr_native_unit_of_measurement == "m"

    def test_altitude_unit_feet_in_fahrenheit_mode(self):
        entity = _sensor_for_key("altitude", temp_unit=UnitOfTemperature.FAHRENHEIT)
        assert entity._attr_native_unit_of_measurement == "ft"


# ---------------------------------------------------------------------------
# AC fault sensor
# ---------------------------------------------------------------------------


class TestACFaultSensor:
    def _make_entity(self, fault_raw=None):
        coord = MagicMock()
        coord.data = {"fault_raw": fault_raw, "mode": 1, "set_temp_c": 22.0,
                      "fan_speed": 3, "swing": 2, "inlet_temp_raw": None}
        entry = MagicMock()
        entry.data = {"address": "AA:BB:CC:DD:EE:FF", "name": "Test AC"}
        entity = VelitACFaultSensorEntity.__new__(VelitACFaultSensorEntity)
        entity.coordinator = coord
        entity._attr_unique_id = "test_ac_fault"
        entity._attr_device_info = MagicMock()
        return entity

    def test_none_when_no_data(self):
        entity = self._make_entity(fault_raw=None)
        assert entity.native_value is None

    def test_raw_bytes_returned_as_hex(self):
        entity = self._make_entity(fault_raw=bytes([0x00, 0x01]))
        assert entity.native_value == "0001"

    def test_none_coordinator_data_returns_none(self):
        entity = self._make_entity()
        entity.coordinator.data = None
        assert entity.native_value is None
