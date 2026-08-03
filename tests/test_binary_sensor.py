"""Unit tests for VelitHeaterFaultBinarySensor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.velit.binary_sensor import VelitHeaterFaultBinarySensor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_UNSET = object()


def _make_entry(address="AA:BB:CC:DD:EE:FF"):
    entry = MagicMock()
    entry.data = {"device_type": "heater", "address": address, "name": "Test Heater"}
    return entry


def _make_coord(fault_code=0, fault_name="No Fault", data=_UNSET):
    coord = MagicMock()
    coord.data = data if data is not _UNSET else {"fault_code": fault_code, "fault_name": fault_name}
    return coord


def _make_entity(fault_code=0, fault_name="No Fault", data=_UNSET):
    coord = _make_coord(fault_code=fault_code, fault_name=fault_name, data=data)
    entry = _make_entry()
    entity = VelitHeaterFaultBinarySensor.__new__(VelitHeaterFaultBinarySensor)
    entity.coordinator = coord
    entity._attr_unique_id = "test_fault"
    entity._attr_name = "Fault Active"
    entity._attr_device_info = MagicMock()
    return entity


# ---------------------------------------------------------------------------
# State properties
# ---------------------------------------------------------------------------


class TestFaultBinarySensorState:
    def test_is_off_when_no_fault(self):
        entity = _make_entity(fault_code=0)
        assert entity.is_on is False

    def test_is_on_when_fault_active(self):
        entity = _make_entity(fault_code=1, fault_name="Ignition Failure")
        assert entity.is_on is True

    def test_is_on_for_any_nonzero_fault_code(self):
        for code in [1, 5, 14, 255]:
            entity = _make_entity(fault_code=code)
            assert entity.is_on is True

    def test_is_none_when_no_data(self):
        entity = _make_entity(data=None)
        assert entity.is_on is None

    def test_extra_attrs_empty_when_no_fault(self):
        entity = _make_entity(fault_code=0)
        assert entity.extra_state_attributes == {}

    def test_extra_attrs_empty_when_no_data(self):
        entity = _make_entity(data=None)
        assert entity.extra_state_attributes == {}

    def test_extra_attrs_contain_fault_info_when_active(self):
        entity = _make_entity(fault_code=1, fault_name="Ignition Failure")
        attrs = entity.extra_state_attributes
        assert attrs["fault_code"] == 1
        assert attrs["fault_name"] == "Ignition Failure"

    def test_unique_id_uses_address(self):
        coord = _make_coord()
        entry = _make_entry(address="11:22:33:44:55:66")
        entity = VelitHeaterFaultBinarySensor.__new__(VelitHeaterFaultBinarySensor)
        entity.coordinator = coord
        entity._attr_unique_id = f"{entry.data['address']}_fault_active"
        assert entity._attr_unique_id == "11:22:33:44:55:66_fault_active"
