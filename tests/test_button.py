"""Unit tests for VelitHeaterPrimeFuelPumpButton and VelitHeaterCleaningButton.

Coordinator client is mocked — tests verify correct commands are sent and that
the prime toggle logic (start / auto-stop / early-stop) behaves as expected.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from custom_components.velit.button import (
    VelitHeaterCleaningButton,
    VelitHeaterPrimeFuelPumpButton,
    _PRIME_AUTO_STOP_SECONDS,
)


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


def _make_prime_button():
    coord = _make_coord()
    entry = _make_entry()
    button = VelitHeaterPrimeFuelPumpButton(coord, entry)
    return button, coord


def _make_cleaning_button():
    coord = _make_coord()
    entry = _make_entry()
    button = VelitHeaterCleaningButton(coord, entry)
    return button, coord


# ---------------------------------------------------------------------------
# Prime button — start and auto-stop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prime_sends_start_command():
    button, coord = _make_prime_button()
    with patch("asyncio.create_task") as mock_task:
        await button.async_press()
    coord._client.send_command.assert_awaited_once_with(0x05, bytes([0x00]))


@pytest.mark.asyncio
async def test_prime_creates_auto_stop_task():
    button, coord = _make_prime_button()
    with patch("asyncio.create_task") as mock_task:
        await button.async_press()
    mock_task.assert_called_once()


@pytest.mark.asyncio
async def test_prime_auto_stop_sends_stop_command():
    button, coord = _make_prime_button()
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await button._auto_stop()
    coord._client.send_command.assert_awaited_once_with(0x06, bytes([0x00]))


@pytest.mark.asyncio
async def test_prime_auto_stop_clears_task_reference():
    button, coord = _make_prime_button()
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await button._auto_stop()
    assert button._prime_task is None


# ---------------------------------------------------------------------------
# Prime button — early stop (press again while priming)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prime_early_stop_cancels_task_and_sends_stop():
    button, coord = _make_prime_button()

    # Simulate an in-progress prime task.
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.done.return_value = False
    button._prime_task = mock_task

    await button.async_press()

    mock_task.cancel.assert_called_once()
    coord._client.send_command.assert_awaited_once_with(0x06, bytes([0x00]))


@pytest.mark.asyncio
async def test_prime_early_stop_clears_task_reference():
    button, coord = _make_prime_button()

    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.done.return_value = False
    button._prime_task = mock_task

    await button.async_press()

    assert button._prime_task is None


@pytest.mark.asyncio
async def test_prime_press_after_completed_task_starts_new_prime():
    """A completed (done) task is not treated as in-progress."""
    button, coord = _make_prime_button()

    completed_task = MagicMock(spec=asyncio.Task)
    completed_task.done.return_value = True
    button._prime_task = completed_task

    with patch("asyncio.create_task"):
        await button.async_press()

    # Should have started, not stopped.
    coord._client.send_command.assert_awaited_once_with(0x05, bytes([0x00]))


# ---------------------------------------------------------------------------
# Cleaning button
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleaning_sends_correct_command():
    button, coord = _make_cleaning_button()
    await button.async_press()
    coord._client.send_command.assert_awaited_once_with(0x09, bytes([0x00]))


# ---------------------------------------------------------------------------
# Entity metadata
# ---------------------------------------------------------------------------

def test_prime_button_unique_id():
    button, _ = _make_prime_button()
    assert button.unique_id == "AA:BB:CC:DD:EE:FF_prime_fuel_pump"


def test_cleaning_button_unique_id():
    button, _ = _make_cleaning_button()
    assert button.unique_id == "AA:BB:CC:DD:EE:FF_cleaning"
