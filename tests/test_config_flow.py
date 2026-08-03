"""Tests for the Velit config flow.

Covers both the Bluetooth discovery path and the manual user path,
including unique ID deduplication and two-device scenarios.

No hardware required — HA test helpers provide mock BLE discovery objects.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.velit.const import DEVICE_TYPE_AC, DEVICE_TYPE_HEATER, DOMAIN

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

HEATER_ADDRESS = "AA:BB:CC:DD:EE:01"
HEATER_NAME = "VELIT-VB52_FHJ/4/1"

AC_ADDRESS = "AA:BB:CC:DD:EE:02"
AC_NAME = "VELIT-AC01_XYZ/1/1"

_PATCH_DISCOVERED = "custom_components.velit.config_flow.async_discovered_service_info"


def _make_discovery(address: str, name: str) -> BluetoothServiceInfoBleak:
    """Return a minimal BluetoothServiceInfoBleak for testing."""
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="local",
        device=None,  # type: ignore[arg-type]
        advertisement=None,  # type: ignore[arg-type]
        connectable=True,
        time=0.0,
        tx_power=None,
    )


# ---------------------------------------------------------------------------
# Bluetooth discovery path
# ---------------------------------------------------------------------------


async def test_bluetooth_discovery_full_flow(hass: HomeAssistant) -> None:
    """Discovery → confirm → device type + name → entry created."""
    discovery = _make_discovery(HEATER_ADDRESS, HEATER_NAME)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=discovery,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "device_type"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"device_type": DEVICE_TYPE_HEATER, CONF_NAME: "Cab Heater"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Cab Heater"
    assert result["data"] == {
        CONF_ADDRESS: HEATER_ADDRESS,
        "device_type": DEVICE_TYPE_HEATER,
        CONF_NAME: "Cab Heater",
    }


async def test_bluetooth_discovery_ac(hass: HomeAssistant) -> None:
    """Discovery flow correctly stores AC device type."""
    discovery = _make_discovery(AC_ADDRESS, AC_NAME)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=discovery,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"device_type": DEVICE_TYPE_AC, CONF_NAME: "Bedroom AC"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["device_type"] == DEVICE_TYPE_AC


async def test_bluetooth_discovery_duplicate_aborts(hass: HomeAssistant) -> None:
    """A second discovery for the same address aborts as already_configured."""
    discovery = _make_discovery(HEATER_ADDRESS, HEATER_NAME)

    # First flow — complete it
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=discovery,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"device_type": DEVICE_TYPE_HEATER, CONF_NAME: "Heater"},
    )

    # Second flow — same address should abort
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=discovery,
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bluetooth_two_different_devices(hass: HomeAssistant) -> None:
    """Two different BLE addresses each produce a separate config entry."""
    for address, name, device_type, label in [
        (HEATER_ADDRESS, HEATER_NAME, DEVICE_TYPE_HEATER, "Heater"),
        (AC_ADDRESS, AC_NAME, DEVICE_TYPE_AC, "AC"),
    ]:
        discovery = _make_discovery(address, name)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_BLUETOOTH},
            data=discovery,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"device_type": device_type, CONF_NAME: label},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 2
    addresses = {e.data[CONF_ADDRESS] for e in entries}
    assert addresses == {HEATER_ADDRESS, AC_ADDRESS}


# ---------------------------------------------------------------------------
# Manual user path — device found in scan
# ---------------------------------------------------------------------------


async def test_user_flow_device_found_in_scan(hass: HomeAssistant) -> None:
    """Scan finds device → picker shown → select device → device type → entry created."""
    discovery = _make_discovery(HEATER_ADDRESS, HEATER_NAME)

    with patch(_PATCH_DISCOVERED, return_value=[discovery]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_ADDRESS: HEATER_ADDRESS}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "device_type"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"device_type": DEVICE_TYPE_HEATER, CONF_NAME: "Van Heater"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == HEATER_ADDRESS
    assert result["data"]["device_type"] == DEVICE_TYPE_HEATER


# ---------------------------------------------------------------------------
# Manual user path — no devices found
# ---------------------------------------------------------------------------


async def test_user_flow_no_devices_then_manual_entry(hass: HomeAssistant) -> None:
    """Scan finds nothing → not_found menu → manual entry → entry created."""
    with patch(_PATCH_DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "not_found"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"next_step_id": "manual"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_ADDRESS: HEATER_ADDRESS}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "device_type"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"device_type": DEVICE_TYPE_HEATER, CONF_NAME: "Van Heater"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == HEATER_ADDRESS


async def test_user_flow_retry_then_found(hass: HomeAssistant) -> None:
    """Scan finds nothing → not_found menu → retry → device appears → entry created."""
    discovery = _make_discovery(HEATER_ADDRESS, HEATER_NAME)

    with patch(_PATCH_DISCOVERED, side_effect=[[], [discovery]]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.MENU
        assert result["step_id"] == "not_found"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"next_step_id": "retry"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_ADDRESS: HEATER_ADDRESS}
    )
    assert result["step_id"] == "device_type"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"device_type": DEVICE_TYPE_HEATER, CONF_NAME: "Van Heater"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_user_flow_duplicate_aborts(hass: HomeAssistant) -> None:
    """Manual entry with an already-configured address aborts."""
    # Create an existing entry via the manual path
    with patch(_PATCH_DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_ADDRESS: HEATER_ADDRESS}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"device_type": DEVICE_TYPE_HEATER, CONF_NAME: "Heater"},
    )

    # Second attempt with the same address should abort
    with patch(_PATCH_DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_ADDRESS: HEATER_ADDRESS}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
