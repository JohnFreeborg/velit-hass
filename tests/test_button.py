"""Unit tests for VelitHeaterCleaningButton."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.velit.button import VelitHeaterCleaningButton


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(address="AA:BB:CC:DD:EE:FF"):
    entry = MagicMock()
    entry.data = {"device_type": "heater", "address": address, "name": "Test Heater"}
    return entry


def _make_coord():
    coord = MagicMock()
    coord._client = MagicMock()
    coord._client.send_command = AsyncMock()
    return coord


def _make_cleaning_button():
    coord = _make_coord()
    entry = _make_entry()
    button = VelitHeaterCleaningButton(coord, entry)
    return button, coord


# ---------------------------------------------------------------------------
# Cleaning button
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleaning_sends_correct_command():
    button, coord = _make_cleaning_button()
    await button.async_press()
    coord._client.send_command.assert_awaited_once_with(0x09, bytes([0x00]))


def test_cleaning_button_unique_id():
    button, _ = _make_cleaning_button()
    assert button.unique_id == "AA:BB:CC:DD:EE:FF_cleaning"
