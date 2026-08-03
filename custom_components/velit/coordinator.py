"""DataUpdateCoordinator implementations for Velit heater and AC devices.

Both coordinators share a common base that handles connection lifecycle and
temperature unit detection. Subclasses override _async_poll() to issue the
device-specific queries and return a data dict.

Temperature unit detection (on first connect):
  Query the device's current setpoint. If the value falls in 16–30 it is in
  Celsius mode; 61–86 means Fahrenheit. Anything else falls back to the HA
  system unit preference (hass.config.units.temperature_unit). The detected
  unit is stored and used for all subsequent SET commands and sensor decoding.
  We never send a value that would flip the device's display unit.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ac_client import VelitACClient
from .const import CONF_POLL_INTERVAL, DOMAIN
from .heater_client import VelitHeaterClient
from .packet_utils import fahrenheit_to_celsius

_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL_DEFAULT = 30  # seconds

# Setpoint ranges used to infer whether the device is in Celsius or Fahrenheit mode.
# These ranges are non-overlapping so the unit can be determined unambiguously.
_CELSIUS_SETPOINT_MIN = 16
_CELSIUS_SETPOINT_MAX = 30
_FAHRENHEIT_SETPOINT_MIN = 61
_FAHRENHEIT_SETPOINT_MAX = 86

# Heater fault codes from protocol V1.02.
HEATER_FAULT_CODES: dict[int, str] = {
    0: "No Fault",
    1: "Ignition Failure",
    2: "Abnormal Flame Out",
    3: "Voltage Deviation",
    4: "Heat Exchanger Temp Anomaly",
    5: "Ignition Sensor Fault",
    6: "Outlet Temp Sensor Fault",
    7: "Fuel Pump Fault",
    8: "Fan Fault",
    9: "Inlet Temp Sensor Fault",
    10: "Glow Plug Fault",
    11: "Operating Ambient Temp Anomaly",
    12: "Altitude Out of Range",
    13: "Fan Blockage Fault",
    14: "CO Pollution Exceeded",
    15: "LIN Communication Fault",
}

# Heater machine states from Query 1 response (offset 3 of data payload).
HEATER_MACHINE_STATES: dict[int, str] = {
    0: "Standby",
    1: "Normal",
    2: "Cooling Down",
    3: "Overtemp Standby",
    4: "Cleaning",
    5: "Clean Complete",
}


class _VelitBaseCoordinator(DataUpdateCoordinator):
    """Shared base for Velit coordinators.

    Manages connect/disconnect lifecycle and temperature unit detection.
    Subclasses implement _async_poll() to issue device-specific queries.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        name: str,
    ) -> None:
        poll_seconds = entry.options.get(CONF_POLL_INTERVAL, POLL_INTERVAL_DEFAULT)
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=timedelta(seconds=poll_seconds),
            always_update=False,
        )
        self._entry = entry
        self._address: str = entry.data["address"]
        self.domain = DOMAIN
        # Detected on first connect — preserved for the lifetime of the entry.
        self.temp_unit: str = UnitOfTemperature.CELSIUS
        # Prime pump state — owned by VelitHeaterFuelPrimingSwitch, read by the
        # countdown sensor. Stored here so both entities share it without coupling.
        self.priming: bool = False
        self.prime_remaining: int = 0
        self._prime_tick_callbacks: list = []

    def register_prime_tick(self, callback) -> None:
        """Register a callback to fire on each prime countdown tick."""
        self._prime_tick_callbacks.append(callback)

    def _notify_prime_tick(self) -> None:
        """Notify all registered prime tick listeners (e.g. countdown sensor)."""
        for cb in self._prime_tick_callbacks:
            cb()

    async def async_connect(self) -> None:
        """Connect the BLE client. Called from async_setup_entry."""
        await self._client.connect()  # type: ignore[attr-defined]

    async def async_disconnect(self) -> None:
        """Disconnect the BLE client. Called from async_unload_entry."""
        await self._client.disconnect()  # type: ignore[attr-defined]

    def _detect_temp_unit(self, setpoint: int) -> str:
        """Infer device temperature unit from the current setpoint value.

        Returns UnitOfTemperature.CELSIUS or FAHRENHEIT. Falls back to the
        HA system unit when the setpoint is outside both known ranges (e.g.
        on first boot or after a fault reset where the value is 0x00).
        """
        if _CELSIUS_SETPOINT_MIN <= setpoint <= _CELSIUS_SETPOINT_MAX:
            return UnitOfTemperature.CELSIUS
        if _FAHRENHEIT_SETPOINT_MIN <= setpoint <= _FAHRENHEIT_SETPOINT_MAX:
            return UnitOfTemperature.FAHRENHEIT
        # Indeterminate — honour whatever the user has configured in HA.
        ha_unit = self.hass.config.units.temperature_unit
        _LOGGER.debug(
            "Setpoint %d outside both unit ranges; falling back to HA system unit %s",
            setpoint,
            ha_unit,
        )
        return ha_unit

    def to_celsius(self, value: float) -> float:
        """Convert a value in the device's active unit to Celsius for HA reporting."""
        if self.temp_unit == UnitOfTemperature.FAHRENHEIT:
            return fahrenheit_to_celsius(value)
        return float(value)

    @abstractmethod
    async def _async_poll(self) -> dict:
        """Issue device queries and return a data dict. Raise UpdateFailed on error."""

    async def _async_update_data(self) -> dict:
        """Called by DataUpdateCoordinator on each poll cycle."""
        try:
            data = await self._async_poll()
        except UpdateFailed:
            raise
        except Exception as exc:
            raise UpdateFailed(f"Unexpected error polling {self._address}: {exc}") from exc

        self._update_fault_issue(data)
        return data

    def _update_fault_issue(self, data: dict) -> None:
        """Raise or clear a Repairs issue based on the current fault code.

        A repair issue is created when fault_code is non-zero so the user sees
        a prominent alert in Settings → Repairs with the fault description.
        The issue is auto-resolved when the fault clears.
        """
        fault_code = data.get("fault_code", 0)
        issue_id = f"fault_{self._address}"
        device_name = self._entry.data.get("name", self._address)

        if fault_code != 0:
            fault_name = data.get("fault_name", f"Unknown ({fault_code})")
            ir.async_create_issue(
                self.hass,
                self.domain,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="device_fault",
                translation_placeholders={
                    "device_name": device_name,
                    "fault_name": fault_name,
                    "fault_code": str(fault_code),
                    "fault_code_display": f"E{fault_code:02d}",
                },
                learn_more_url="https://velitcamping.com/pages/velit-heater-error-code-guide",
            )
        else:
            ir.async_delete_issue(self.hass, self.domain, issue_id)


class VelitHeaterCoordinator(_VelitBaseCoordinator):
    """Coordinator for Velit heater devices (protocol V1.02).

    Polls Query 1 (0x0A) for device state and Query 2 (0x0B) for sensor
    readings on each cycle. Temperature unit is detected from the Query 1
    setpoint on first connect.

    Data dict keys:
      fault_code        int     — raw fault code (0 = no fault)
      fault_name        str     — human-readable fault description
      work_mode         int     — 1 = Manual, 2 = Thermostat
      current_gear      int     — 1–5
      set_temp_c        float   — target temperature in Celsius
      machine_state     int     — raw machine state code
      machine_state_str str     — human-readable machine state
      heater_power_w    int     — heater power in Watts
      fuel_pump_hz      float   — fuel pump frequency in Hz
      voltage_v         float | None  — supply voltage in V (None if unavailable)
      fan_rpm           int | None    — fan speed in RPM (None if unavailable)
      inlet_temp_c      float | None  — inlet temperature in Celsius (None if unavailable)
      casing_temp_c     float | None  — casing temperature in Celsius (None if unavailable)
      outlet_temp_c     float | None  — outlet temperature in Celsius (None if unavailable)
      altitude          int | None    — altitude in device unit (None if unavailable)
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry, name=f"Velit Heater {entry.data['address']}")
        self._client = VelitHeaterClient(self._address)
        self._unit_detected = False

    async def _async_poll(self) -> dict:
        q1 = await self._client.send_command(0x0A, bytes([0x00]))
        if q1 is None:
            raise UpdateFailed("No response to Query 1 (0x0A)")

        q2 = await self._client.send_command(0x0B, bytes([0x00]))
        if q2 is None:
            raise UpdateFailed("No response to Query 2 (0x0B)")

        return self._parse(q1["data"], q2["data"])

    def _parse(self, q1_data: bytes, q2_data: bytes) -> dict:
        """Parse Query 1 and Query 2 data payloads into a normalised data dict."""
        # Query 1 layout: [fault][work_mode][gear][set_temp][machine_state][power][pump_freq]
        fault_code = q1_data[0]
        work_mode = q1_data[1]
        current_gear = q1_data[2]
        set_temp_raw = q1_data[3]
        machine_state = q1_data[4]
        heater_power_w = q1_data[5]
        fuel_pump_hz = q1_data[6] / 10.0

        # Detect unit on first successful poll.
        if not self._unit_detected:
            self.temp_unit = self._detect_temp_unit(set_temp_raw)
            self._unit_detected = True
            _LOGGER.debug("Heater %s: detected temp unit %s", self._address, self.temp_unit)

        set_temp_c = self.to_celsius(set_temp_raw)

        # Query 2 layout: [fault][voltage 2B][fan_rpm 2B][inlet 2B][casing 2B][outlet 2B][alt 2B]
        # Each sensor pair is big-endian unsigned int. 0xFFFF = sensor unavailable.
        def _read_u16(data: bytes, offset: int) -> int:
            return (data[offset] << 8) | data[offset + 1]

        def _sensor_temp(raw: int) -> float | None:
            if raw == 0xFFFF:
                return None
            # Protocol offset encoding: raw - 50 = °C, raw - 60 = °F.
            if self.temp_unit == UnitOfTemperature.FAHRENHEIT:
                return float(raw - 60)
            return float(raw - 50)

        voltage_raw = _read_u16(q2_data, 1)   # offset 0 = fault byte, skip
        fan_raw = _read_u16(q2_data, 3)
        inlet_raw = _read_u16(q2_data, 5)
        casing_raw = _read_u16(q2_data, 7)
        outlet_raw = _read_u16(q2_data, 9)
        alt_raw = _read_u16(q2_data, 11)

        inlet_native = _sensor_temp(inlet_raw)
        casing_native = _sensor_temp(casing_raw)
        outlet_native = _sensor_temp(outlet_raw)

        return {
            "fault_code": fault_code,
            "fault_name": HEATER_FAULT_CODES.get(fault_code, f"Unknown ({fault_code})"),
            "work_mode": work_mode,
            "current_gear": current_gear,
            "set_temp_c": set_temp_c,
            "machine_state": machine_state,
            "machine_state_str": HEATER_MACHINE_STATES.get(machine_state, f"Unknown ({machine_state})"),
            "heater_power_w": heater_power_w,
            "fuel_pump_hz": fuel_pump_hz,
            "voltage_v": voltage_raw / 10.0 if voltage_raw != 0xFFFF else None,
            "fan_rpm": fan_raw if fan_raw != 0xFFFF else None,
            "inlet_temp_c": self.to_celsius(inlet_native) if inlet_native is not None else None,
            "casing_temp_c": self.to_celsius(casing_native) if casing_native is not None else None,
            "outlet_temp_c": self.to_celsius(outlet_native) if outlet_native is not None else None,
            "altitude": alt_raw if alt_raw != 0xFFFF else None,
        }


class VelitACCoordinator(_VelitBaseCoordinator):
    """Coordinator for Velit AC devices (protocol V1.01).

    Polls mode (0x02), temperature (0x03), fan speed (0x04), swing (0x10),
    inlet air temperature (0x07), and fault info (0x0B) on each cycle.

    Inlet temperature and fault response formats are not fully documented —
    both are queried and the raw response is stored. Decoded values will be
    added once response formats are confirmed via hardware capture.

    Data dict keys:
      mode              int     — current operation mode code (see AC protocol 0x02)
      set_temp_c        float   — current setpoint in Celsius
      fan_speed         int     — current fan speed 1–5
      swing             int     — 1 = swinging, 2 = stopped (raw device value)
      inlet_temp_raw    bytes | None  — raw 0x07 response data (format unconfirmed)
      fault_raw         bytes | None  — raw 0x0B response data (format unconfirmed)
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry, name=f"Velit AC {entry.data['address']}")
        self._client = VelitACClient(self._address)
        self._unit_detected = False

    async def _async_poll(self) -> dict:
        mode_rsp = await self._client.send_command(0x02, bytes([0x00]))
        if mode_rsp is None:
            raise UpdateFailed("No response to mode query (0x02)")

        temp_rsp = await self._client.send_command(0x03, bytes([0x00]))
        if temp_rsp is None:
            raise UpdateFailed("No response to temperature query (0x03)")

        fan_rsp = await self._client.send_command(0x04, bytes([0x00]))
        if fan_rsp is None:
            raise UpdateFailed("No response to fan speed query (0x04)")

        swing_rsp = await self._client.send_command(0x10, bytes([0x00]))
        if swing_rsp is None:
            raise UpdateFailed("No response to swing query (0x10)")

        # Inlet temp and fault formats unconfirmed — log raw, do not raise on failure.
        inlet_rsp = await self._client.send_command(0x07, bytes([0x00]))
        fault_rsp = await self._client.send_command(0x0B, bytes([0x00]))

        set_temp_raw = temp_rsp["data"][0]
        if not self._unit_detected:
            self.temp_unit = self._detect_temp_unit(set_temp_raw)
            self._unit_detected = True
            _LOGGER.debug("AC %s: detected temp unit %s", self._address, self.temp_unit)

        return {
            "mode": mode_rsp["data"][0],
            "set_temp_c": self.to_celsius(set_temp_raw),
            "fan_speed": fan_rsp["data"][0],
            "swing": swing_rsp["data"][0],
            "inlet_temp_raw": inlet_rsp["data"] if inlet_rsp else None,
            "fault_raw": fault_rsp["data"] if fault_rsp else None,
        }
