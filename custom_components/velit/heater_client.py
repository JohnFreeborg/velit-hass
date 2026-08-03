"""Heater BLE packet builder, response parser, and connection client.

Protocol: Velit Air Heater Communication Protocol V1.02.
Packet construction, parsing, and BLE connection management are all here.

Packet structure (command, master to slave):
  [0x55][length][master 4B][slave 4B][func][data N][checksum 2B]

Packet structure (response, slave to master):
  [0xAA][length][master 4B][slave 4B][SF 2B][func][data N][checksum 2B]

Checksum: unsigned 16-bit sum of all bytes from start flag through data,
high byte first. Verified against 11 of 12 protocol examples — the shutdown
command example (func 0x02) appears to contain a typo in the source document
and does not match the formula. Hardware verification required.

Addressing: slave address 0x0000002D is a fixed constant on all known Velit
heaters — verified on hardware (2026-03-25). Master address is arbitrary.
Device isolation is provided by the BLE connection, not protocol addressing.
See HEATER_MASTER_ADDR / HEATER_SLAVE_ADDR in const.py.
"""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient, BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic

from .const import HEATER_MASTER_ADDR, HEATER_SLAVE_ADDR, UUID_READ_NOTIFY, UUID_WRITE

_LOGGER = logging.getLogger(__name__)

# Packet framing constants
_START_CMD = 0x55
_START_RSP = 0xAA
_MFG_CODE = bytes([0x53, 0x46])  # "SF" — present in every response

# Minimum valid response length: AA + len + master(4) + slave(4) + SF(2) + func + data(>=1) + cksum(2)
_MIN_RESPONSE_LEN = 16


def build_command(
    master_addr: bytes,
    slave_addr: bytes,
    func: int,
    data: bytes,
) -> bytes:
    """Build a complete heater command packet ready to write to the BLE characteristic.

    Args:
        master_addr: 4-byte master address.
        slave_addr:  4-byte slave device address.
        func:        1-byte function code.
        data:        Command payload bytes (variable length).

    Returns:
        Complete packet as bytes including start flag and checksum.

    Raises:
        ValueError: If address lengths are not 4 bytes.
    """
    if len(master_addr) != 4 or len(slave_addr) != 4:
        raise ValueError("master_addr and slave_addr must each be 4 bytes")

    # Length = bytes after the length field: master(4) + slave(4) + func(1) + data(N) + checksum(2)
    length = 4 + 4 + 1 + len(data) + 2

    header = bytes([_START_CMD, length]) + master_addr + slave_addr + bytes([func]) + data
    checksum = _heater_checksum(header)
    return header + checksum


def parse_response(raw: bytes) -> dict | None:
    """Parse a heater response packet.

    Validates start flag, minimum length, manufacturer code, and checksum.

    Returns a dict with keys: func, data, master_addr, slave_addr.
    Returns None if the packet is invalid.
    """
    if len(raw) < _MIN_RESPONSE_LEN:
        _LOGGER.debug("Response too short: %d bytes", len(raw))
        return None

    if raw[0] != _START_RSP:
        _LOGGER.debug("Unexpected start byte: 0x%02X", raw[0])
        return None

    # Manufacturer code at bytes 10–11 (after AA + len + master(4) + slave(4))
    if raw[10:12] != _MFG_CODE:
        _LOGGER.debug("Manufacturer code mismatch: %s", raw[10:12].hex())
        return None

    if not _validate_response_checksum(raw):
        _LOGGER.debug("Checksum mismatch on response: %s", raw.hex())
        return None

    master_addr = raw[2:6]
    slave_addr = raw[6:10]
    func = raw[12]
    # Data sits between func byte and the 2-byte checksum at the end
    data = raw[13:-2]

    return {
        "func": func,
        "data": data,
        "master_addr": master_addr,
        "slave_addr": slave_addr,
    }


def _heater_checksum(payload: bytes) -> bytes:
    """Compute the 2-byte LRC2 checksum for a heater packet.

    Sum of all bytes in payload (start flag through data), returned
    as 2 bytes big-endian (high byte first).
    """
    total = sum(payload) & 0xFFFF
    return bytes([total >> 8, total & 0xFF])


def _validate_response_checksum(raw: bytes) -> bool:
    """Return True if the response checksum is correct.

    Checksum covers all bytes from start flag through data (everything
    except the final 2 checksum bytes).
    """
    expected = _heater_checksum(raw[:-2])
    return raw[-2:] == expected


# ---------------------------------------------------------------------------
# BLE connection layer
# ---------------------------------------------------------------------------

_RECONNECT_DELAY_INITIAL = 1.0   # seconds
_RECONNECT_DELAY_MAX = 30.0      # seconds
_COMMAND_TIMEOUT = 5.0           # seconds to wait for a notification response


class VelitHeaterClient:
    """BLE client for the Velit heater protocol (V1.02).

    Manages connection, command serialisation, and notification handling.

    Slave address 0x0000002D is a fixed constant on all known Velit heaters
    (verified on hardware, 2026-03-25). Master address is arbitrary. Both
    default to the confirmed values from const.py.
    """

    def __init__(
        self,
        address: str | BLEDevice,
        master_addr: bytes = HEATER_MASTER_ADDR,
        slave_addr: bytes = HEATER_SLAVE_ADDR,
    ) -> None:
        """
        Args:
            address:     BLE device address or BLEDevice from discovery.
            master_addr: 4-byte master address (arbitrary — device ignores its value).
            slave_addr:  4-byte slave address (fixed constant on all known Velit heaters;
                         device isolation is provided by the BLE connection, not this field).
        """
        self._address = address
        self._master_addr = master_addr
        self._slave_addr = slave_addr
        self._client: BleakClient | None = None
        self._queue: asyncio.Queue[
            tuple[int, bytes, asyncio.Future[dict | None]]
        ] = asyncio.Queue()
        # Resolved by the notification handler for whichever command is in flight.
        self._pending: asyncio.Future[dict | None] | None = None
        self._connected = False
        self._queue_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the device and subscribe to response notifications."""
        client = BleakClient(
            self._address,
            disconnected_callback=self._on_disconnect,
        )
        self._client = client
        try:
            await client.connect()
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
        self, func: int, data: bytes, timeout: float = _COMMAND_TIMEOUT
    ) -> dict | None:
        """Queue a command and wait for the parsed response.

        Returns the parsed response dict or None on timeout or connection error.
        """
        if not self._connected:
            _LOGGER.debug("send_command called while not connected")
            return None

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict | None] = loop.create_future()
        await self._queue.put((func, data, fut))
        try:
            # Allow extra headroom beyond the per-command timeout so the caller
            # future is never orphaned inside the queue.
            return await asyncio.wait_for(fut, timeout=timeout + 2.0)
        except asyncio.TimeoutError:
            _LOGGER.warning("send_command timed out waiting for result (func 0x%02X)", func)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _queue_runner(self) -> None:
        """Process commands one at a time until disconnected."""
        while self._connected:
            try:
                func, data, caller_fut = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            loop = asyncio.get_running_loop()
            self._pending = loop.create_future()
            result: dict | None = None

            try:
                packet = build_command(
                    self._master_addr, self._slave_addr, func, data
                )
                await self._client.write_gatt_char(  # type: ignore[union-attr]
                    UUID_WRITE, packet, response=True
                )
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(self._pending), timeout=_COMMAND_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "No notification within %.0fs for func 0x%02X",
                        _COMMAND_TIMEOUT, func,
                    )
            except Exception as exc:
                _LOGGER.warning("Command write failed (func 0x%02X): %s", func, exc)
            finally:
                self._pending = None
                self._queue.task_done()

            if not caller_fut.done():
                caller_fut.set_result(result)

    def _on_notification(
        self, _char: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Handle an incoming notification from the device."""
        parsed = parse_response(bytes(data))
        if self._pending and not self._pending.done():
            self._pending.set_result(parsed)
        elif parsed:
            _LOGGER.debug("Unsolicited notification: func 0x%02X", parsed.get("func"))

    def _on_disconnect(self, _client: BleakClient) -> None:
        """Called by bleak when the connection is lost unexpectedly."""
        if not self._connected:
            # The disconnect fired during a failed connect() attempt —
            # _connected was never set True. connect() handles its own cleanup;
            # do not start a reconnect loop here.
            return
        self._connected = False
        _LOGGER.warning("Connection lost to %s", self._address)
        # Cancel any in-flight command future so the caller does not hang.
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
