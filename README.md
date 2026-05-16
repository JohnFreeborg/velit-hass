# velit-hass

Home Assistant integration for Velit Camping heaters and air conditioners via Bluetooth.

Control your Velit device from Home Assistant — set temperature, change modes, monitor
sensors, and build automations on device state and fault conditions.

Why use this instead of the phone app?
* Stays connected to each device (no more having to choose and connect frequently)
* Automatic history logging very helpful for troubleshooting or understanding power/time/temperature trade offs
* Can control and monitor either AC or heater without having to disconnect/reconnect to the other device
* Home Assistant dashboard on the phone is handier and more convenient than isolated phone app

---

## Supported Devices

| Device | Type | Tested | Firmware |
|---|---|---|---|
| Velit 4000P (fixed) | Heater | Yes | 3.13 / 3.26 / 3.62 |
| Velit Portable | Heater | No | — |
| Velit 2000R | AC | In progress | — |
| Velit 2000R Mini | AC | No | — |
| Velit 3000R | AC | No | — |
| Velit 2000U | AC | No | — |
| Velit V3 | AC | No | — |

If you have tested this integration on a device not listed above, please open an issue
with the model and firmware version so the table can be updated.

---

> [!WARNING]
> **Read this before proceeding.**
>
> This project involves interfacing with a **combustion heater** that produces **open flame, high heat, and carbon monoxide**. Improper installation, software faults, or loss of communication between the controller and heater can result in **fire, carbon monoxide poisoning, serious injury, or death**.
>
> **By using any part of this project — code, documentation, or captures — you accept full and sole responsibility for your implementation, installation, and any consequences that result.** The author(s) of this project provide it as-is, with no warranty of any kind, expressed or implied. This project is not affiliated with VELIT Cooling & Heating, LLC or any related entity.
>
> **Minimum precautions you should take:**
> - Install a working CO detector in any enclosed space where the heater operates
> - Never leave a combustion heater running unattended without independent safety mechanisms (CO detector, thermal cutoff, smoke alarm)
> - Test all control and shutdown paths thoroughly before relying on this system
> - Retain the ability to cut heater power independently of this controller at all times
> - Consult a qualified installer if you are uncertain about any aspect of the wiring or installation

---

## Features

**Heater**
- Power on/off, manual mode, and thermostat mode
- Gear/fan speed control (levels 1–5) — not yet hardware-validated
- Target temperature
- Sensor entities: inlet temperature, altitude, fault code, machine state
- Fault Active binary sensor — suitable for automations and dashboard cards
- HA Repairs issue raised automatically on fault, with a link to the Velit error guide
- BLE connection switch — release the device to the Velit mobile app without removing
  the integration
- Fuel pump prime switch — runs a 30-second prime cycle with auto-stop; companion
  countdown sensor ticks in real time
- Cleaning switch — initiates the residual fuel cleaning cycle and reflects cycle state
- Firmware version shown in the HA device info panel
- Bluetooth auto-discovery

**Air Conditioner**
- Power on/off
- Modes: cool, heat, fan only, dry
- Presets: energy saving, sleep, turbo
- Fan speed control (levels 1–5)
- Swing control
- Target temperature
- Sensor entities: inlet temperature, fault code
- BLE connection switch — release the device to the Velit mobile app without removing
  the integration
- Bluetooth auto-discovery

---

## Requirements

- Home Assistant 2024.1 or later
- A Bluetooth adapter accessible to your HA instance
- A Velit heater or air conditioner with Bluetooth enabled

---

## Installation

### HACS (recommended)

A pre-release beta is available via HACS as a custom repository:

1. Open HACS in Home Assistant.
2. Go to **Integrations** and click the three-dot menu in the top right.
3. Select **Custom repositories**.
4. Add `https://github.com/JohnFreeborg/velit-hass` with category **Integration**.
5. Search for **Velit** in HACS and click **Download**.
6. In the download dialog, enable **Show beta versions** and select the pre-release version.
7. Restart Home Assistant.

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

## Options

After setup, you can adjust per-device options from **Settings → Devices & Services →
Velit → Configure**:

- **Poll interval** — how often the integration queries the device (5–300 seconds,
  default 30). The interval automatically drops to 5 seconds during active state
  transitions and returns to the configured value once the device settles.
- **Mark unavailable on fault** — when enabled, the climate entity becomes unavailable
  while a fault code is active, preventing commands that the device would silently ignore.

---

## Notes

- The integration communicates directly with the device over Bluetooth. Your HA instance
  must have a Bluetooth adapter within range of the device.
- The physical temperature display unit on the device (°C or °F) is preserved — the
  integration detects the current unit on connect and does not change it. Home Assistant
  handles display conversion based on your system preferences.
- Commands sent from HA take effect immediately; state is refreshed straight after.

---

## Troubleshooting

**Device not discovered automatically**
- Confirm the device is powered on and within Bluetooth range.
- Close the Velit mobile app on all nearby phones before running setup — the app holds
  the Bluetooth connection and will prevent Home Assistant from finding the device.
- Check that your HA instance has a working Bluetooth adapter (Settings → System → Hardware).
- Try adding the device manually using its Bluetooth address.

**Integration shows unavailable**
- The device may be out of Bluetooth range or powered off.
- If another app (e.g. the Velit mobile app) is connected to the device, the integration
  may not be able to connect. Disconnect the other app and reload the integration.
- Use the **BLE Connection** switch on the device page to manually release or re-acquire
  the Bluetooth connection.
- Check the HA logs (Settings → System → Logs) for error details.

**Heater takes a long time to turn off**
- After a heat cycle ends, the heater runs a cooling-down cycle before shutting off
  completely. This typically takes around 3 minutes and is normal device behaviour.
  The climate card will reflect the cooling state during this period.

**Fault sensor shows an error code**
- Fault descriptions are listed on the device page in Home Assistant.
- A Repairs issue is raised automatically with a link to the Velit error guide.
- Refer to your device manual for guidance on each fault type.
- You can build automations to alert on specific fault conditions using the Fault Active
  binary sensor as a trigger.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for branching, commit, and testing guidelines.

Issues and pull requests are welcome at https://github.com/JohnFreeborg/velit-hass
