"""AC BLE packet builder, response parser, and connection client.

Protocol: Velit Air Conditioner Communication Protocol V1.01.
Packet construction, parsing, and BLE connection management are all here.

Packet structure (command, app to AC):
  [0x5A][0x5A][length][product_code][func][data N][checksum 1B][0x0D][0x0A]

Packet structure (response, AC to app):
  [0x5A][0x5A][length][product_code][func][data N][checksum 1B][0x0D][0x0A]

Checksum: sum of all bytes from frame header through data, low byte only.
Verified against the single known example in the protocol document.
Additional examples needed for full confidence — hardware verification required.

Length field: based on the single documented example (1 data byte, length=6),
the length value appears to equal len(data) + 5. This is a theory and requires
hardware verification with multi-byte data payloads.

Note: command transmission interval must be at least 400ms.
On no response within 3 seconds, retry once. After 3 total failures,
mark the device as unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import time

from bleak import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import (
    AC_COMMAND_INTERVAL_MS,
    AC_MAX_RETRIES,
    AC_RESPONSE_TIMEOUT_S,
    UUID_READ_NOTIFY,
    UUID_WRITE,
)

_LOGGER = logging.getLogger(__name__)

# Packet framing
_HEADER = bytes([0x5A, 0x5A])
_TERMINATOR = bytes([0x0D, 0x0A])

# Product code used in the documented example. The protocol does not
# explicitly document all valid product code values — needs verification.
DEFAULT_PRODUCT_CODE = 0x01

# Minimum valid response: header(2) + len(1) + product(1) + func(1) + data(>=1) + cksum(1) + term(2)
_MIN_RESPONSE_LEN = 9


def build_command(func: int, data: bytes, product_code: int = DEFAULT_PRODUCT_CODE) -> bytes:
    """Build a complete AC command packet ready to write to the BLE characteristic.

    Args:
        func:         1-byte function code.
        data:         Command payload bytes (variable length).
        product_code: 1-byte product identifier (default 0x01 per protocol example).

    Returns:
        Complete packet as bytes including header, checksum, and terminator.
    """
    # Length theory: len(data) + 5, based on the single documented example.
    # The example has 1 data byte and length=6: 1 + 5 = 6.
    length = len(data) + 5

    payload = _HEADER + bytes([length, product_code, func]) + data
    checksum = _ac_checksum(payload)
    return payload + bytes([checksum]) + _TERMINATOR


def parse_response(raw: bytes) -> dict | None:
    """Parse an AC response packet.

    Validates header, terminator, minimum length, and checksum.

    Returns a dict with keys: func, data, product_code.
    Returns None if the packet is invalid.
    """
    if len(raw) < _MIN_RESPONSE_LEN:
        _LOGGER.debug("Response too short: %d bytes", len(raw))
        return None

    if raw[:2] != _HEADER:
        _LOGGER.debug("Unexpected header: %s", raw[:2].hex())
        return None

    if raw[-2:] != _TERMINATOR:
        _LOGGER.debug("Missing terminator: %s", raw[-2:].hex())
        return None

    if not _validate_response_checksum(raw):
        _LOGGER.debug("Checksum mismatch on response: %s", raw.hex())
        return None

    product_code = raw[3]
    func = raw[4]
    # Data sits between func byte and checksum+terminator at the end
    data = raw[5:-3]

    return {
        "func": func,
        "data": data,
        "product_code": product_code,
    }


def _ac_checksum(payload: bytes) -> int:
    """Compute the 1-byte checksum for an AC packet.

    Sum of all bytes in payload (header through data), low byte only.
    """
    return sum(payload) & 0xFF


def _validate_response_checksum(raw: bytes) -> bool:
    """Return True if the response checksum is correct.

    Checksum covers all bytes from header through data (raw[:-3]),
    excluding the checksum byte and 0x0D 0x0A terminator.
    """
    expected = _ac_checksum(raw[:-3])
    return raw[-3] == expected


# ---------------------------------------------------------------------------
# BLE connection layer
# ---------------------------------------------------------------------------

_RECONNECT_DELAY_INITIAL = 1.0  # seconds
_RECONNECT_DELAY_MAX = 30.0     # seconds
_COMMAND_INTERVAL = AC_COMMAND_INTERVAL_MS / 1000.0  # convert to seconds


class VelitACClient:
    """BLE client for the Velit AC protocol (V1.01).

    Manages connection, command serialisation, inter-command timing,
    retry logic, and notification handling.

    Timing rules per protocol spec:
    - Minimum 400ms between commands.
    - On no response within 3s, retry once.
    - After AC_MAX_RETRIES consecutive failures, mark the device unavailable.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        address: str | BLEDevice,
        product_code: int = DEFAULT_PRODUCT_CODE,
    ) -> None:
        """
        Args:
            hass:         HomeAssistant instance — used to resolve BLEDevice at connect time.
            address:      BLE device address or BLEDevice from discovery.
            product_code: 1-byte product identifier (default 0x01).
        """
        self._hass = hass
        self._address: str = address.address if isinstance(address, BLEDevice) else address
        self._product_code = product_code
        self._client: BleakClientWithServiceCache | None = None
        self._queue: asyncio.Queue[
            tuple[int, bytes, asyncio.Future[dict | None]]
        ] = asyncio.Queue()
        self._pending: asyncio.Future[dict | None] | None = None
        self._connected = False
        self.unavailable = False
        self._consecutive_failures = 0
        self._last_command_time: float = 0.0
        self._queue_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the device and subscribe to response notifications."""
        device = bluetooth.async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )
        if device is None:
            raise RuntimeError(f"Device {self._address} not found in Bluetooth scanner cache")

        # establish_connection handles transient failures and uses the service cache
        # to skip GATT re-discovery on reconnect, per HA Bluetooth integration guidelines.
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self._address,
            disconnected_callback=self._on_disconnect,
        )
        self._client = client
        try:
            await client.start_notify(UUID_READ_NOTIFY, self._on_notification)
        except Exception:
            # Disconnect before re-raising so BlueZ releases this connection and
            # any stale notification subscription — prevents "Notify acquired" on
            # the next attempt.
            try:
                await client.disconnect()
            except Exception:
                pass
            raise
        self._connected = True
        self.unavailable = False
        self._consecutive_failures = 0
        self._queue_task = asyncio.create_task(self._queue_runner())
        _LOGGER.info("Connected to %s", self._address)

    async def disconnect(self) -> None:
        """Disconnect cleanly, stopping the command queue first."""
        self._connected = False

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()

        if self._queue_task and not self._queue_task.done():
            self._queue_task.cancel()

        if self._client and self._client.is_connected:
            try:
                await self._client.stop_notify(UUID_READ_NOTIFY)
            except Exception:
                pass
            await self._client.disconnect()

        _LOGGER.info("Disconnected from %s", self._address)

    @property
    def connected(self) -> bool:
        """Return True when the BLE connection is established and ready."""
        return self._connected

    async def send_command(
        self,
        func: int,
        data: bytes,
        timeout: float = float(AC_RESPONSE_TIMEOUT_S),
    ) -> dict | None:
        """Queue a command and wait for the parsed response.

        Returns the parsed response dict, or None if the command failed or
        timed out. The caller does not need to handle retry logic —
        the queue runner applies one automatic retry per the protocol spec.
        Returns None and sets self.unavailable = True after AC_MAX_RETRIES
        consecutive failures.
        """
        if self.unavailable:
            _LOGGER.debug("send_command skipped — device marked unavailable")
            return None

        if not self._connected:
            _LOGGER.debug("send_command called while not connected")
            return None

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict | None] = loop.create_future()
        await self._queue.put((func, data, fut))
        try:
            return await asyncio.wait_for(fut, timeout=timeout * 2 + _COMMAND_INTERVAL + 2.0)
        except asyncio.TimeoutError:
            _LOGGER.warning("send_command timed out (func 0x%02X)", func)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _queue_runner(self) -> None:
        """Process commands one at a time, enforcing inter-command timing."""
        while self._connected:
            try:
                func, data, caller_fut = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            result = await self._execute_with_retry(func, data)
            self._queue.task_done()

            if not caller_fut.done():
                caller_fut.set_result(result)

    async def _execute_with_retry(
        self, func: int, data: bytes
    ) -> dict | None:
        """Send a command with one automatic retry per the protocol spec.

        Returns the parsed response or None. Updates failure tracking and
        sets self.unavailable if AC_MAX_RETRIES is reached.
        """
        for attempt in range(2):  # initial attempt + one retry
            await self._enforce_interval()
            result = await self._write_and_wait(func, data)

            if result is not None:
                self._consecutive_failures = 0
                return result

            if attempt == 0:
                _LOGGER.debug(
                    "No response for func 0x%02X, retrying once", func
                )

        # Both attempts failed.
        self._consecutive_failures += 1
        _LOGGER.warning(
            "Command failed (func 0x%02X), consecutive failures: %d/%d",
            func, self._consecutive_failures, AC_MAX_RETRIES,
        )

        if self._consecutive_failures >= AC_MAX_RETRIES:
            self.unavailable = True
            _LOGGER.error(
                "Device at %s marked unavailable after %d consecutive failures",
                self._address, self._consecutive_failures,
            )

        return None

    async def _write_and_wait(self, func: int, data: bytes) -> dict | None:
        """Write one command and wait up to AC_RESPONSE_TIMEOUT_S for a response."""
        loop = asyncio.get_running_loop()
        self._pending = loop.create_future()
        result: dict | None = None

        try:
            packet = build_command(func, data, self._product_code)
            await self._client.write_gatt_char(  # type: ignore[union-attr]
                UUID_WRITE, packet, response=True
            )
            self._last_command_time = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(self._pending),
                    timeout=float(AC_RESPONSE_TIMEOUT_S),
                )
            except asyncio.TimeoutError:
                pass
        except Exception as exc:
            _LOGGER.warning("Write failed (func 0x%02X): %s", func, exc)
        finally:
            self._pending = None

        return result

    async def _enforce_interval(self) -> None:
        """Sleep until at least AC_COMMAND_INTERVAL_MS has elapsed since the last write."""
        elapsed = time.monotonic() - self._last_command_time
        wait = _COMMAND_INTERVAL - elapsed
        if wait > 0:
            await asyncio.sleep(wait)

    def _on_notification(
        self, _char: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Handle an incoming notification from the device."""
        parsed = parse_response(bytes(data))
        if self._pending and not self._pending.done():
            self._pending.set_result(parsed)
        elif parsed:
            _LOGGER.debug("Unsolicited notification: func 0x%02X", parsed.get("func"))

    def _on_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        """Called by bleak when the connection is lost unexpectedly."""
        if not self._connected:
            # The disconnect fired during a failed connect() attempt —
            # _connected was never set True. connect() handles its own cleanup;
            # do not start a reconnect loop here.
            return
        self._connected = False
        _LOGGER.warning("Connection lost to %s", self._address)
        if self._pending and not self._pending.done():
            self._pending.set_result(None)
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        delay = _RECONNECT_DELAY_INITIAL
        while not self._connected:
            _LOGGER.info(
                "Attempting reconnect to %s in %.0fs", self._address, delay
            )
            await asyncio.sleep(delay)
            try:
                await self.connect()
                return
            except Exception as exc:
                _LOGGER.warning("Reconnect failed: %s", exc)
                delay = min(delay * 2, _RECONNECT_DELAY_MAX)
