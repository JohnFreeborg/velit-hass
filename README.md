# velit-hass

Home Assistant integration for Velit Camping heaters and air conditioners via Bluetooth.

Control your Velit device from Home Assistant — set temperature, change modes, monitor
sensors, and build automations on device state and fault conditions.

Why use this instead of the phone app?
* Stays connected to each device (no more having to choose and connect frequently)
* Automatic history logging for troubleshooting and understanding power/time/temperature trade offs
* Can control and monitor both AC and heater without having to disconnect/reconnect to the 'other device'
* Home Assistant dashboard on the phone is more convenient than isolated phone app

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

## Supported Devices

| Device | Type | Tested | Firmware |
|---|---|---|---|
| Velit 4000P (fixed) | Heater | Yes | 3.13 / 3.26 / 3.62 / 3.90 |
| Velit Portable | Heater | No | — |
| Velit 2000R | AC | In progress | — |
| Velit 2000R Mini | AC | No | — |
| Velit 3000R | AC | Yes | — |
| Velit 2000U | AC | No | — |
| Velit V3 | AC | No | — |

If you have tested this integration on a device not listed above, please open an issue
with the model and firmware version so the table can be updated.

---

## Features

**Heater**
- Power on/off, with Auto (thermostat) and Manual presets
- Fan Level control (1–5) in Manual mode
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
- Optional Error 16 self-heal (see Options)
- Bluetooth auto-discovery

**Air Conditioner**
- Power on/off
- Modes: cool, fan only
- Presets: Eco, Sleep, Turbo
- Fan Level control (1–5)
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

1. Open HACS in Home Assistant.
2. Go to **Integrations** and click the three-dot menu in the top right.
3. Select **Custom repositories**.
4. Add `https://github.com/JohnFreeborg/velit-hass` with category **Integration**.
5. Search for **Velit** in HACS and click **Download**.
6. To test a beta, enable **Show beta versions** in the download dialog and select the
   pre-release version.
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
  transitions and briefly after a command, then returns to the configured value.
- **Error 16 self-heal** (heater only) — off by default. Some heaters show Error 16 on
  the display without reporting it over Bluetooth; while it is up, the inlet temperature
  stops updating. When enabled, the integration re-sends the current setpoint (the value
  does not change) to clear it once any of these triggers fires while the heater is in
  Standby:
  - the inlet temperature has not changed for a set number of hours (1–24, default 3)
  - an optional reference temperature sensor moves by a set amount (default 5 °F / 3 °C)
    while the inlet holds still
  - optionally, the altitude changes by a set amount (default 100 ft / 30 m) while the
    inlet holds still

  Thresholds use your HA unit system. It never acts while the heater is running, priming,
  cleaning, or right after a command, and at most once an hour. A Last Self-heal
  diagnostic sensor shows when it last acted and why.
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
  completely. This typically takes about 5 minutes and is normal device behaviour.
  The climate card will reflect the cooling state during this period.
- The heater ignores turn-on while cooling down. Wait for it to return to Standby.

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
