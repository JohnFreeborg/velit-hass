"""Unit tests for VelitHeaterBLESwitch and VelitHeaterFuelPrimingSwitch."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.velit.switch import (
    VelitHeaterBLESwitch,
    VelitHeaterFuelPrimingSwitch,
    _PRIME_DURATION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(address="AA:BB:CC:DD:EE:FF"):
    entry = MagicMock()
    entry.data = {"device_type": "heater", "address": address, "name": "Test Heater"}
    return entry


def _make_entity(connected=True):
    coord = MagicMock()
    coord._client = MagicMock()
    coord._client.connected = connected
    coord._client.connect = AsyncMock()
    coord._client.disconnect = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    entry = _make_entry()
    entity = VelitHeaterBLESwitch.__new__(VelitHeaterBLESwitch)
    entity.coordinator = coord
    entity._attr_unique_id = f"{entry.data['address']}_ble_connection"
    entity._attr_name = "BLE Connection"
    entity._attr_device_info = MagicMock()
    # async_write_ha_state needs to be a no-op in tests
    entity.async_write_ha_state = MagicMock()
    return entity, coord


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class TestBLESwitchState:
    def test_is_on_when_connected(self):
        entity, _ = _make_entity(connected=True)
        assert entity.is_on is True

    def test_is_off_when_disconnected(self):
        entity, _ = _make_entity(connected=False)
        assert entity.is_on is False

    def test_always_available(self):
        # Must return True even when disconnected so the user can toggle back on.
        entity, _ = _make_entity(connected=False)
        assert entity.available is True


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


class TestBLESwitchActions:
    async def test_turn_off_calls_disconnect(self):
        entity, coord = _make_entity(connected=True)
        await entity.async_turn_off()
        coord._client.disconnect.assert_awaited_once()

    async def test_turn_off_writes_state(self):
        entity, _ = _make_entity(connected=True)
        await entity.async_turn_off()
        entity.async_write_ha_state.assert_called_once()

    async def test_turn_on_calls_connect(self):
        entity, coord = _make_entity(connected=False)
        await entity.async_turn_on()
        coord._client.connect.assert_awaited_once()

    async def test_turn_on_requests_refresh_on_success(self):
        entity, coord = _make_entity(connected=False)
        await entity.async_turn_on()
        coord.async_request_refresh.assert_awaited_once()

    async def test_turn_on_writes_state(self):
        entity, _ = _make_entity(connected=False)
        await entity.async_turn_on()
        entity.async_write_ha_state.assert_called_once()

    async def test_turn_on_does_not_raise_on_connect_failure(self):
        entity, coord = _make_entity(connected=False)
        coord._client.connect = AsyncMock(side_effect=Exception("BLE busy"))
        # Should not raise — logs warning and writes state
        await entity.async_turn_on()
        entity.async_write_ha_state.assert_called_once()

    def test_unique_id(self):
        entity, _ = _make_entity()
        assert entity._attr_unique_id == "AA:BB:CC:DD:EE:FF_ble_connection"


# ---------------------------------------------------------------------------
# Prime switch helpers
# ---------------------------------------------------------------------------


def _make_prime_entity(connected=True):
    coord = MagicMock()
    coord._client = MagicMock()
    coord._client.connected = connected
    coord._client.send_command = AsyncMock()
    coord.priming = False
    coord.prime_remaining = 0
    coord._notify_prime_tick = MagicMock()
    entry = _make_entry()
    entity = VelitHeaterFuelPrimingSwitch.__new__(VelitHeaterFuelPrimingSwitch)
    entity.coordinator = coord
    entity._attr_unique_id = f"{entry.data['address']}_fuel_priming"
    entity._attr_device_info = MagicMock()
    entity._prime_task = None
    entity.async_write_ha_state = MagicMock()
    return entity, coord


# ---------------------------------------------------------------------------
# Prime switch — state
# ---------------------------------------------------------------------------


class TestPrimeSwitchState:
    def test_is_on_when_priming(self):
        entity, coord = _make_prime_entity()
        coord.priming = True
        assert entity.is_on is True

    def test_is_off_when_not_priming(self):
        entity, coord = _make_prime_entity()
        coord.priming = False
        assert entity.is_on is False

    def test_available_when_connected(self):
        entity, _ = _make_prime_entity(connected=True)
        assert entity.available is True

    def test_unavailable_when_disconnected(self):
        entity, _ = _make_prime_entity(connected=False)
        assert entity.available is False

    def test_unique_id(self):
        entity, _ = _make_prime_entity()
        assert entity._attr_unique_id == "AA:BB:CC:DD:EE:FF_fuel_priming"


# ---------------------------------------------------------------------------
# Prime switch — actions
# ---------------------------------------------------------------------------


class TestPrimeSwitchActions:
    async def test_turn_on_sends_start_command(self):
        entity, coord = _make_prime_entity()
        with patch("asyncio.create_task"):
            await entity.async_turn_on()
        coord._client.send_command.assert_awaited_once_with(0x05, bytes([0x00]))

    async def test_turn_on_sets_priming_state(self):
        entity, coord = _make_prime_entity()
        with patch("asyncio.create_task"):
            await entity.async_turn_on()
        assert coord.priming is True
        assert coord.prime_remaining == _PRIME_DURATION

    async def test_turn_on_notifies_tick(self):
        entity, coord = _make_prime_entity()
        with patch("asyncio.create_task"):
            await entity.async_turn_on()
        coord._notify_prime_tick.assert_called_once()

    async def test_turn_on_is_noop_when_already_priming(self):
        entity, coord = _make_prime_entity()
        coord.priming = True
        await entity.async_turn_on()
        coord._client.send_command.assert_not_awaited()

    async def test_turn_off_cancels_task(self):
        entity, coord = _make_prime_entity()
        coord.priming = True
        mock_task = MagicMock()
        mock_task.done.return_value = False
        entity._prime_task = mock_task
        await entity.async_turn_off()
        mock_task.cancel.assert_called_once()

    async def test_turn_off_is_noop_when_not_priming(self):
        entity, coord = _make_prime_entity()
        mock_task = MagicMock()
        entity._prime_task = mock_task
        await entity.async_turn_off()
        mock_task.cancel.assert_not_called()


# ---------------------------------------------------------------------------
# Prime switch — countdown task
# ---------------------------------------------------------------------------


class TestPrimeSwitchRunPrime:
    async def test_auto_stop_sends_stop_command(self):
        entity, coord = _make_prime_entity()
        coord.prime_remaining = 1
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await entity._run_prime()
        coord._client.send_command.assert_awaited_once_with(0x06, bytes([0x00]))

    async def test_auto_stop_resets_state(self):
        entity, coord = _make_prime_entity()
        coord.prime_remaining = 1
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await entity._run_prime()
        assert coord.priming is False
        assert coord.prime_remaining == 0
        assert entity._prime_task is None

    async def test_cancelled_sends_stop_command(self):
        entity, coord = _make_prime_entity()
        coord.prime_remaining = 30
        with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
            await entity._run_prime()
        coord._client.send_command.assert_awaited_once_with(0x06, bytes([0x00]))

    async def test_cancelled_resets_state(self):
        entity, coord = _make_prime_entity()
        coord.prime_remaining = 30
        with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
            await entity._run_prime()
        assert coord.priming is False
        assert coord.prime_remaining == 0
        assert entity._prime_task is None

    async def test_tick_decrements_remaining(self):
        entity, coord = _make_prime_entity()
        coord.prime_remaining = 2
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await entity._run_prime()
        # After 2 ticks, prime_remaining should be 0 (finally sets it to 0)
        assert coord.prime_remaining == 0

    async def test_tick_notifies_on_each_step(self):
        entity, coord = _make_prime_entity()
        coord.prime_remaining = 2
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await entity._run_prime()
        # 2 ticks during loop + 1 in finally = 3 total
        assert coord._notify_prime_tick.call_count == 3
