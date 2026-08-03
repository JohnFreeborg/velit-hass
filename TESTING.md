# Testing Guide

This guide covers how to run the unit tests locally and how to validate the
integration against real hardware.

---

## Unit Tests

Unit tests cover packet building and parsing, config flow logic, coordinator
data parsing, climate entity state and actions, and sensor values. No Bluetooth
hardware is required.

### Setup

```bash
pip install -r requirements_test.txt
```

### Run

```bash
pytest tests/
```

For coverage:

```bash
pytest tests/ --cov=custom_components.velit --cov-report=term-missing
```

Tests are also run automatically on every pull request via GitHub Actions.

---

## Hardware Testing

Some behaviour can only be verified against a real device — BLE connectivity,
command acknowledgement, and sensor readings. If you have a supported device,
please run through the relevant checklist below and note your results in the PR.

### Setup

Install the integration on a Home Assistant instance with a Bluetooth adapter
within range of your device. See [README.md](README.md) for install steps.

### Heater Checklist

Work through these in order. The heater should be powered on throughout.

**Connection**
- [ ] Integration sets up without error in HA logs
- [ ] Device appears in Settings → Devices & Services
- [ ] All sensor entities show values (not unavailable)

**Sensors — verify values are plausible**
- [ ] Inlet temperature matches physical expectation for ambient conditions
- [ ] Supply voltage reads approximately 12–13V (12V DC system)
- [ ] Fault sensor shows "No Fault" during normal operation
- [ ] Machine state reflects actual device state (standby when off, normal when running)

**Climate entity**
- [ ] Current temperature displayed matches inlet sensor
- [ ] Target temperature matches what is set on the physical device
- [ ] HVAC mode reflects device state (off/heat/fan only)
- [ ] Preset reflects device mode (manual/thermostat)

**Commands — confirm each is acknowledged by the device**
- [ ] Set HVAC mode to Heat — device starts up
- [ ] Set HVAC mode to Fan Only — device runs ventilation only
- [ ] Set HVAC mode to Off — device shuts down
- [ ] Set target temperature — device acknowledges new setpoint
- [ ] Set fan mode (gear) 1 through 5 — device changes gear level
- [ ] Switch preset manual ↔ thermostat — device changes mode

**Temperature unit**
- [ ] If device display is in °F, HA still shows temperature in your system unit
  and does not change the physical display unit
- [ ] If device display is in °C, same applies

**Fault codes**
- [ ] Note any fault codes encountered during testing and confirm the sensor
  description matches the physical fault indicator on the device

### AC Checklist

**Note: AC hardware testing has not yet been completed. The AC entity is
implemented based on the protocol specification but has not been verified
against a real unit. All AC results should be reported as new findings.**

**Connection**
- [ ] Integration sets up without error
- [ ] Device appears in Settings → Devices & Services

**Climate entity**
- [ ] HVAC mode: cool, heat, fan only, dry — each acknowledged by device
- [ ] Presets: energy saving, sleep, turbo — each acknowledged
- [ ] Fan speed 1–5 — each acknowledged
- [ ] Swing on/off — acknowledged
- [ ] Target temperature — acknowledged

**Open questions requiring hardware confirmation**
- [ ] Are Fan mode (0x03) and Vent mode (0x08) functionally different?
  Note observed behaviour for each.
- [ ] What does the fault query (0x0B) response look like in normal operation?
  Capture the raw bytes from the HA diagnostic sensor and include in your PR.
- [ ] What does the inlet temperature query (0x07) response look like?
  Note the raw value and the ambient temperature at time of capture.

### Reporting Results

Include the following in your PR description or as a comment:

- Device model and firmware version
- HA version and hardware (e.g. HA Yellow, generic x86)
- Which checklist items passed, failed, or could not be tested
- Any unexpected behaviour or log output

---

## What to Look for in Logs

Enable debug logging for the integration by adding to your HA `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.velit: debug
```

Relevant log messages to watch for:

| Message | Meaning |
|---|---|
| `Connected to <address>` | BLE connection established |
| `Detected temp unit °C/°F` | Unit detection result on first connect |
| `No response to Query 1` | Device did not respond — check range and power |
| `Connection lost to <address>` | Unexpected disconnect — integration will attempt reconnect |
| `Attempting reconnect in Xs` | Reconnect backoff in progress |
