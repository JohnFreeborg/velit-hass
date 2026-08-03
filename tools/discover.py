"""Standalone BLE discovery and diagnostic script for Velit devices.

Run this script against real hardware to capture advertisement data,
probe the device's expected packet addresses, and confirm the protocol
behaves as documented.

Usage:
    python tools/discover.py [--address AA:BB:CC:DD:EE:FF]

Without --address: scans for all nearby Velit devices and prints their
full advertisement data. Useful for capturing manufacturer/service data
that may reveal device type (heater vs AC) without requiring user input.

With --address: connects to the specified device and:
  1. Reads all GATT services and characteristics.
  2. Subscribes to the notification characteristic (FFe1).
  3. Sends heater Query Command 1 (func 0x0A) and Query Command 2 (0x0B)
     with a range of slave address candidates and prints raw responses.
  4. Prints all unsolicited notifications received for 5 seconds.

Hardware verification targets:
  - Confirm heater Query 1 / Query 2 response structure against V1.02 doc.
  - Determine which master/slave address values the device accepts.
  - Capture advertisement bytes to check for device type differentiators.
  - Confirm shutdown command checksum (doc shows 0x0085, formula gives 0x0091).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

# Allow running from the project root without installing the package.
sys.path.insert(0, ".")

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# BLE advertisement local name prefixes used by Velit devices.
# Source: manifest.json bluetooth matchers.
_VELIT_NAME_PREFIXES = ("VELIT", "VLIT", "D30")

# BEKEN Corp manufacturer ID (0x585A = 22618), present in all known Velit
# advertisements. Some firmware versions advertise with the MAC as the local
# name rather than a VELIT* prefix — manufacturer ID is the fallback.
_VELIT_MANUFACTURER_ID = 22618

# Characteristic UUIDs from const.py (duplicated here so this script has
# no dependency on the HA package tree).
_UUID_READ_NOTIFY = "0000ffe1-0000-1000-8000-00805f9b34fb"
_UUID_WRITE = "0000ffe2-0000-1000-8000-00805f9b34fb"

# Address candidates to probe. The heater protocol uses 4-byte master/slave
# addresses that are unrelated to BLE MAC addresses. Their origin is unknown.
# This list tries the most likely values first: the protocol document examples,
# all-zeros, and all-ones.
_MASTER_CANDIDATES = [
    bytes([0x00, 0x00, 0x00, 0x01]),  # protocol document example
    bytes([0x00, 0x00, 0x00, 0x00]),
    bytes([0xFF, 0xFF, 0xFF, 0xFF]),
]
_SLAVE_CANDIDATES = [
    bytes([0x00, 0x00, 0x00, 0x2D]),  # protocol document example (45)
    bytes([0x00, 0x00, 0x00, 0x01]),
    bytes([0x00, 0x00, 0x00, 0x00]),
    bytes([0xFF, 0xFF, 0xFF, 0xFF]),
]

# Heater query function codes (V1.02 Command Table 2).
_HEATER_QUERY_1 = 0x0A  # Query Command 1 — machine state, temps, fan RPM, etc.
_HEATER_QUERY_2 = 0x0B  # Query Command 2 — inlet/casing/outlet temps, voltage

_SCAN_TIMEOUT = 10.0  # seconds


def _is_velit(device: BLEDevice, adv: AdvertisementData) -> bool:
    name = adv.local_name or device.name or ""
    if name.startswith(_VELIT_NAME_PREFIXES):
        return True
    return _VELIT_MANUFACTURER_ID in adv.manufacturer_data


def _heater_checksum(payload: bytes) -> bytes:
    """2-byte LRC2 checksum: sum of all payload bytes, big-endian."""
    total = sum(payload) & 0xFFFF
    return bytes([total >> 8, total & 0xFF])


def _build_heater_packet(master: bytes, slave: bytes, func: int, data: bytes) -> bytes:
    length = 4 + 4 + 1 + len(data) + 2
    header = bytes([0x55, length]) + master + slave + bytes([func]) + data
    return header + _heater_checksum(header)


def _print_adv(device: BLEDevice, adv: AdvertisementData) -> None:
    print(f"\nDevice:   {device.address}")
    print(f"  Name:   {adv.local_name or device.name or '(none)'}")
    print(f"  RSSI:   {device.rssi} dBm")
    if adv.manufacturer_data:
        for company_id, payload in adv.manufacturer_data.items():
            print(f"  Manufacturer data (0x{company_id:04X}): {payload.hex(' ')}")
    else:
        print("  Manufacturer data: (none)")
    if adv.service_uuids:
        for uuid in adv.service_uuids:
            print(f"  Service UUID: {uuid}")
    else:
        print("  Service UUIDs: (none)")
    if adv.service_data:
        for uuid, payload in adv.service_data.items():
            print(f"  Service data ({uuid}): {payload.hex(' ')}")


# ---------------------------------------------------------------------------
# Scan mode
# ---------------------------------------------------------------------------


async def scan() -> None:
    """Scan for Velit devices and print full advertisement data."""
    print(f"Scanning {_SCAN_TIMEOUT:.0f}s for Velit devices ({', '.join(_VELIT_NAME_PREFIXES)}* or manufacturer ID {_VELIT_MANUFACTURER_ID})...")
    found: list[tuple[BLEDevice, AdvertisementData]] = []

    def callback(device: BLEDevice, adv: AdvertisementData) -> None:
        if _is_velit(device, adv):
            found.append((device, adv))
            _print_adv(device, adv)

    async with BleakScanner(detection_callback=callback):
        await asyncio.sleep(_SCAN_TIMEOUT)

    print(f"\nScan complete. Found {len(found)} Velit device(s).")
    if found:
        print("\nTo probe a device, run:")
        for device, _ in found:
            print(f"  python tools/discover.py --address {device.address}")


# ---------------------------------------------------------------------------
# Probe mode
# ---------------------------------------------------------------------------


async def probe(address: str) -> None:
    """Connect to a device and run diagnostic commands."""
    notifications: list[bytes] = []

    def on_notify(_char, data: bytearray) -> None:
        raw = bytes(data)
        notifications.append(raw)
        print(f"  << notification: {raw.hex(' ')}")

    print(f"\nConnecting to {address}...")
    async with BleakClient(address) as client:
        print("Connected.")

        # List services and characteristics.
        print("\nGATT services:")
        for service in client.services:
            print(f"  Service: {service.uuid}")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"    Char: {char.uuid}  [{props}]")

        # Subscribe to notifications.
        await client.start_notify(_UUID_READ_NOTIFY, on_notify)
        print(f"\nSubscribed to notifications on {_UUID_READ_NOTIFY}")

        # Probe heater Query 1 and Query 2 with address candidates.
        print("\nProbing heater Query 1 (0x0A) and Query 2 (0x0B)...")
        print("(Assumes heater protocol — AC will not respond to these packets)")

        for master in _MASTER_CANDIDATES:
            for slave in _SLAVE_CANDIDATES:
                for func in (_HEATER_QUERY_1, _HEATER_QUERY_2):
                    pkt = _build_heater_packet(master, slave, func, bytes([0x00]))
                    print(
                        f"\n  >> master={master.hex()} slave={slave.hex()} "
                        f"func=0x{func:02X}  packet: {pkt.hex(' ')}"
                    )
                    try:
                        await client.write_gatt_char(_UUID_WRITE, pkt, response=True)
                    except Exception as exc:
                        print(f"     write error: {exc}")
                    await asyncio.sleep(0.5)

        # Collect any remaining unsolicited notifications.
        print("\nListening 5s for any further notifications...")
        await asyncio.sleep(5.0)

        await client.stop_notify(_UUID_READ_NOTIFY)

    print(f"\nProbe complete. Total notifications received: {len(notifications)}")
    if not notifications:
        print(
            "No notifications received. Possible causes:\n"
            "  - Device is an AC unit (different protocol — try AC-specific commands)\n"
            "  - Master/slave address combination not accepted\n"
            "  - Device requires pairing before responding\n"
            "  - Characteristic UUID is different on this firmware version"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Velit BLE discovery and diagnostic tool")
    parser.add_argument(
        "--address",
        metavar="ADDR",
        help="BLE address to probe directly (skip scan)",
    )
    args = parser.parse_args()

    if args.address:
        asyncio.run(probe(args.address))
    else:
        asyncio.run(scan())


if __name__ == "__main__":
    main()
