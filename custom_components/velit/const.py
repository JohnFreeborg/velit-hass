"""Constants for the Velit integration."""

DOMAIN = "velit"

# Options keys — stored in config entry options dict.
CONF_POLL_INTERVAL = "poll_interval"
CONF_UNAVAILABLE_ON_FAULT = "unavailable_on_fault"

# Device type identifiers stored in config entry data.
# Determined during config flow — cannot be inferred from BLE advertisement alone (open question).
DEVICE_TYPE_HEATER = "heater"
DEVICE_TYPE_AC = "ac"

# BLE characteristic UUIDs — shared across both heater and AC devices.
# Source: Velit communication protocol documents V1.01 / V1.02.
UUID_SERVICE = "0000ffe0-0000-1000-8000-00805f9b34fb"
UUID_READ_NOTIFY = "0000ffe1-0000-1000-8000-00805f9b34fb"
UUID_WRITE = "0000ffe2-0000-1000-8000-00805f9b34fb"

# BLE advertisement local names observed for Velit devices.
# Note: "D30" is a generic name — may match non-Velit devices. Requires hardware verification.
BLE_LOCAL_NAMES = ["VELIT", "VLIT", "D30"]

# Heater protocol constants (V1.02)
HEATER_START_BYTE = 0x55
HEATER_RESPONSE_START = 0xAA
HEATER_MFG_CODE = bytes([0x53, 0x46])  # "SF" — validated in every heater response

# Heater packet addressing — verified on hardware (2026-03-25, Velit 4000P).
# The slave address 0x0000002D is a fixed constant on all known Velit heaters — it is NOT
# derived from the BLE MAC address and does not uniquely identify a device at the protocol
# level. Device isolation is provided entirely by the BLE connection (we connect to a specific
# BLE address); the slave address in the packet is effectively a protocol constant.
# The master address is arbitrary — the device responds regardless of its value.
# Cross-device contamination (reported in campsite scenarios) is a Velit app issue caused by
# connecting to the wrong BLE device, not a protocol addressing issue.
HEATER_MASTER_ADDR = bytes([0x00, 0x00, 0x00, 0x01])
HEATER_SLAVE_ADDR = bytes([0x00, 0x00, 0x00, 0x2D])

# AC protocol constants (V1.01)
AC_START_BYTES = bytes([0x5A, 0x5A])
AC_END_BYTES = bytes([0x0D, 0x0A])
AC_COMMAND_INTERVAL_MS = 400  # minimum ms between commands per protocol spec
AC_RESPONSE_TIMEOUT_S = 3     # seconds before a command is considered failed
AC_MAX_RETRIES = 3            # attempts before marking device unavailable

# Temperature encoding — both devices transmit hex values, not raw degrees.
# C range: 16–30 displayed, 0x10–0x1E transmitted
# F range: 61–86 displayed, 0x3D–0x56 transmitted
# Formula: F = 1.8 * C + 32
HEATER_MIN_TEMP_C = 16
HEATER_MAX_TEMP_C = 30
AC_MIN_TEMP_C = 17
AC_MAX_TEMP_C = 30
