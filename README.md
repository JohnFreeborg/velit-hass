# velit-hass

Home Assistant integration for Velit Camping heaters and air conditioners via Bluetooth.

Control your Velit device from Home Assistant — set temperature, change modes, monitor
sensors, and build automations on device state and fault conditions.

---

## Supported Devices

| Device | Type | Tested | Firmware |
|---|---|---|---|
| Velit 4000P (fixed) | Heater | Yes | 3.62 |
| Velit Portable | Heater | No | — |
| Velit 2000R | AC | No | — |
| Velit 2000R Mini | AC | No | — |
| Velit 3000R | AC | No | — |
| Velit 2000U | AC | No | — |

If you have tested this integration on a device not listed above, please open an issue
with the model and firmware version so the table can be updated.

---

## Features

**Heater**
- Power on/off, manual mode, and thermostat mode
- Gear/fan speed control (levels 1–5)
- Target temperature
- Sensor entities: inlet temperature, casing temperature, outlet temperature, supply voltage, fan RPM, altitude
- Fault code sensor with human-readable fault descriptions
- Machine state sensor (standby, normal, cooling down, etc.)
- Bluetooth auto-discovery

**Air Conditioner**
- Power on/off
- Modes: cool, heat, fan only, dry
- Presets: energy saving, sleep, turbo
- Fan speed control (levels 1–5)
- Swing control
- Target temperature
- Bluetooth auto-discovery

---

## Requirements

- Home Assistant 2024.1 or later
- A Bluetooth adapter accessible to your HA instance
- A Velit heater or air conditioner with Bluetooth enabled

---

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant.
2. Go to **Integrations** and click the three-dot menu in the top right.
3. Select **Custom repositories**.
4. Add `https://github.com/JohnFreeborg/velit-hass` with category **Integration**.
5. Search for **Velit** in HACS and click **Download**.
6. Restart Home Assistant.

### Manual

1. Download or clone this repository.
2. Copy the `custom_components/velit/` directory into your HA configuration directory:
   ```
   <config>/custom_components/velit/
   ```
3. Restart Home Assistant.

---

## Setup

### Automatic discovery

If your Velit device is powered on and within Bluetooth range, Home Assistant will detect
it automatically and show a notification in **Settings → Devices & Services** prompting
you to complete setup.

### Manual setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Velit**.
3. Enter the Bluetooth address of your device.
4. Select the device type (Heater or Air Conditioner) and give it a name.

---

## Notes

- The integration communicates directly with the device over Bluetooth. Your HA instance
  must have a Bluetooth adapter within range of the device.
- The physical temperature display unit on the device (°C or °F) is preserved — the
  integration detects the current unit on connect and does not change it. Home Assistant
  handles display conversion based on your system preferences.
- Poll interval is 30 seconds. Commands sent from HA take effect immediately and state
  is refreshed straight after.

---

## Troubleshooting

**Device not discovered automatically**
- Confirm the device is powered on and within Bluetooth range.
- Check that your HA instance has a working Bluetooth adapter (Settings → System → Hardware).
- Try adding the device manually using its Bluetooth address.

**Integration shows unavailable**
- The device may be out of Bluetooth range or powered off.
- If another app (e.g. the Velit mobile app) is connected to the device, the integration
  may not be able to connect. Disconnect the other app and reload the integration.
- Check the HA logs (Settings → System → Logs) for error details.

**Fault sensor shows an error code**
- Fault descriptions are listed on the device page in Home Assistant.
- Refer to your device manual for guidance on each fault type.
- You can build automations to alert on specific fault conditions using the Fault sensor
  as a trigger.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for branching, commit, and testing guidelines.

Issues and pull requests are welcome at https://github.com/JohnFreeborg/velit-hass

