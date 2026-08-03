# discover.py

This is the tool we use to talk directly to Velit hardware outside of Home Assistant —
finding devices, capturing raw advertisement bytes, confirming protocol behaviour, and
validating that write commands do what we think they do. When we need to verify a theory
about a new device model, check a firmware difference, or test a write command before
wiring it into the integration, this is the starting point.

---

## Requirements

Install `bleak` directly (the HA virtual environment is not suitable):

```
pip install bleak
```

Run from the project root:

```
python tools/discover.py [options]
```

---

## Platform notes

**macOS** — Bluetooth devices are identified by a system-assigned UUID rather than
a MAC address. Run an open scan first to discover the UUID, then pass it to
`--address`:

```
python tools/discover.py
python tools/discover.py --address 04C09073-7029-C3AF-B7D0-832646ADA442
```

---

## Usage

### Open scan

```
python tools/discover.py
```

Scans 10 seconds for any Velit device matched by name prefix (`VELIT*`, `VLIT*`,
`D30*`), manufacturer ID (`0x585A`), or service UUID (`0000ffe0`). Prints full
advertisement data for each device found, then suggests the `--address` command
to probe each one.

Use this when you don't know the device address, or to capture raw advertisement
bytes for a new device model.

### Probe a specific device

```
python tools/discover.py --address <ADDRESS>
```

Captures advertisement data for the target address, then connects and runs a full
diagnostic sequence:

1. Prints advertisement bytes (manufacturer ID, service UUIDs, RSSI).
2. Reads and prints all GATT services and characteristics.
3. Subscribes to the notification characteristic (`ffe1`).
4. Sends a JSON firmware query (AC OTA protocol) — AC units may respond here.
5. Probes the heater protocol: sends Query 1 (`0x0A`) and Query 2 (`0x0B`) with
   multiple master/slave address candidates and prints all raw responses.
6. If heater responses were received, queries the heater firmware version (`0x6A`).
7. If no heater responses were received, probes the AC protocol: sends all seven
   AC query functions (`0x5A5A` framing) and prints responses.
8. Prints a protocol identification summary (heater / AC / no response).

### Write flags (heater only)

These flags send commands that physically actuate the device. Only run them when
you intend to test write behaviour on real hardware.

**`--test-writes`**

```
python tools/discover.py --address <ADDRESS> --test-writes
```

Sends `0x03` (start ventilation) from standby, waits 3 seconds to observe any
physical response, then sends `0x04` (stop ventilation). Useful for confirming
whether the device has a functional fan-only mode. Only runs if the device is
confirmed as a heater.

**`--test-fan-sequence`**

```
python tools/discover.py --address <ADDRESS> --test-fan-sequence
```

Tests fan-only mode as a mode switch rather than a cold-start command:

1. Sends `0x01` (start heat, manual mode).
2. Polls Query 1 every 5 seconds until `machine_state` is non-zero (device running),
   up to 90 seconds.
3. Sends `0x03` (switch to fan mode).
4. Polls Query 1 four more times (20 seconds) to observe whether `machine_state`
   changes.
5. Sends `0x02` (stop / power off).

The device will briefly attempt to start a heat cycle before the mode switch is
sent. Only runs if the device is confirmed as a heater.

---

## Reading the output

**Advertisement section** — manufacturer data, service UUIDs, and RSSI as seen
in the raw BLE advertisement. Useful for identifying device type differentiators
across models.

**GATT section** — all services and characteristics with their properties
(`read`, `write`, `notify`, etc.). Confirms the characteristic UUIDs the device
exposes.

**Notification lines** — every unsolicited or response packet received on `ffe1`,
printed as raw hex as they arrive:

```
<< notification: aa 14 00 00 00 01 00 00 00 2d 53 46 0a 00 02 05 4a 01 50 00 02 31
```

**Protocol summary** — at the end of a probe run, the tool prints how many
notifications each protocol received and identifies the device as heater, AC, or
unknown.

---

## Caveats

- `--test-writes` and `--test-fan-sequence` physically actuate the device.
  Do not use them unless you intend to test write behaviour on real hardware.
- The `0000ffe0` service UUID is generic and may match non-Velit devices.
  The open scan will surface any nearby device advertising this UUID.
- Heater address candidates (`master` / `slave` fields in the packet) are probed
  exhaustively. The known-good combination (`master=00000001`, `slave=0000002D`)
  is confirmed on the Velit 4000P. Other models may differ.
- AC product code `0x01` is used for all AC queries. Whether this is correct for
  all AC models is unconfirmed.
