"""Unit tests for VelitHeaterClient and VelitACClient internals.

No BLE hardware — exercises notification/command correlation, the connect()
re-entry guard, and AC unavailable recovery. Connection establishment itself
is covered by hardware testing; these tests only cover logic that can be
driven without a link.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.velit.ac_client import VelitACClient
from custom_components.velit.heater_client import VelitHeaterClient

ADDRESS = "AA:BB:CC:DD:EE:FF"
MASTER = bytes([0x00, 0x00, 0x00, 0x01])
SLAVE = bytes([0x00, 0x00, 0x00, 0x2D])


def _heater_rsp(func: int, data: int = 0x01) -> bytearray:
    """Build a valid heater response packet for the given func."""
    base = bytes([0xAA, 0x0E]) + MASTER + SLAVE + bytes([0x53, 0x46, func, data])
    total = sum(base) & 0xFFFF
    return bytearray(base + bytes([total >> 8, total & 0xFF]))


def _ac_rsp(func: int, data: int = 0x01) -> bytearray:
    """Build a valid AC response packet for the given func."""
    payload = bytes([0x5A, 0x5A, 0x06, 0x01, func, data])
    return bytearray(payload + bytes([sum(payload) & 0xFF, 0x0D, 0x0A]))


# ---------------------------------------------------------------------------
# Heater client — notification/command correlation
# ---------------------------------------------------------------------------


class TestHeaterNotificationCorrelation:
    def _client_with_pending(self, func: int) -> VelitHeaterClient:
        client = VelitHeaterClient(MagicMock(), ADDRESS)
        client._pending = asyncio.get_running_loop().create_future()
        client._pending_func = func
        return client

    async def test_matching_func_resolves_pending(self):
        client = self._client_with_pending(0x0A)
        client._on_notification(None, _heater_rsp(0x0A))
        assert client._pending.done()
        assert client._pending.result()["func"] == 0x0A

    async def test_mismatched_func_ignored(self):
        # A late Query 1 response must not be delivered as the answer to Query 2.
        client = self._client_with_pending(0x0B)
        client._on_notification(None, _heater_rsp(0x0A))
        assert not client._pending.done()

    async def test_invalid_packet_ignored_while_pending(self):
        # A corrupt notification must not resolve the command as failed —
        # the valid response may still arrive within the timeout.
        client = self._client_with_pending(0x0A)
        client._on_notification(None, bytearray(b"\x00\x01\x02"))
        assert not client._pending.done()

    async def test_unsolicited_notification_without_pending_is_safe(self):
        client = VelitHeaterClient(MagicMock(), ADDRESS)
        client._on_notification(None, _heater_rsp(0x0A))  # must not raise


# ---------------------------------------------------------------------------
# AC client — notification/command correlation
# ---------------------------------------------------------------------------


class TestACNotificationCorrelation:
    def _client_with_pending(self, func: int) -> VelitACClient:
        client = VelitACClient(MagicMock(), ADDRESS)
        client._pending = asyncio.get_running_loop().create_future()
        client._pending_func = func
        return client

    async def test_matching_func_resolves_pending(self):
        client = self._client_with_pending(0x01)
        client._on_notification(None, _ac_rsp(0x01))
        assert client._pending.done()
        assert client._pending.result()["func"] == 0x01

    async def test_mismatched_func_ignored(self):
        client = self._client_with_pending(0x03)
        client._on_notification(None, _ac_rsp(0x01))
        assert not client._pending.done()

    async def test_invalid_packet_ignored_while_pending(self):
        client = self._client_with_pending(0x01)
        client._on_notification(None, bytearray(b"\x5a\x5a"))
        assert not client._pending.done()


# ---------------------------------------------------------------------------
# connect() re-entry guard
# ---------------------------------------------------------------------------


class TestConnectGuard:
    async def test_heater_connect_noop_when_already_connected(self):
        client = VelitHeaterClient(MagicMock(), ADDRESS)
        client._connected = True
        with patch(
            "custom_components.velit.heater_client.bluetooth.async_ble_device_from_address"
        ) as lookup:
            await client.connect()
        lookup.assert_not_called()

    async def test_ac_connect_noop_when_already_connected(self):
        client = VelitACClient(MagicMock(), ADDRESS)
        client._connected = True
        with patch(
            "custom_components.velit.ac_client.bluetooth.async_ble_device_from_address"
        ) as lookup:
            await client.connect()
        lookup.assert_not_called()


# ---------------------------------------------------------------------------
# AC unavailable recovery
# ---------------------------------------------------------------------------


class TestACUnavailableRecovery:
    async def test_successful_response_clears_unavailable(self):
        client = VelitACClient(MagicMock(), ADDRESS)
        client.unavailable = True
        client._enforce_interval = AsyncMock()
        client._write_and_wait = AsyncMock(
            return_value={"func": 0x01, "data": b"\x01", "product_code": 0x01}
        )
        result = await client._execute_with_retry(0x01, b"\x00")
        assert result is not None
        assert client.unavailable is False

    async def test_send_command_skips_within_probe_interval(self):
        client = VelitACClient(MagicMock(), ADDRESS)
        client._connected = True
        client.unavailable = True
        client._last_unavailable_probe = time.monotonic()
        assert await client.send_command(0x01, b"\x00") is None

    async def test_failure_keeps_unavailable(self):
        client = VelitACClient(MagicMock(), ADDRESS)
        client.unavailable = True
        client._consecutive_failures = 3
        client._enforce_interval = AsyncMock()
        client._write_and_wait = AsyncMock(return_value=None)
        result = await client._execute_with_retry(0x01, b"\x00")
        assert result is None
        assert client.unavailable is True
