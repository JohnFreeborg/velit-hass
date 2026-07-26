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
_ACTIVE_POLL_INTERVAL = timedelta(seconds=5)
# Number of consecutive poll failures tolerated before entities go unavailable.
# A single BLE notification timeout is common during active device operations
# and should not immediately surface as "unavailable" in the UI.
_POLL_FAILURE_TOLERANCE = 2

# Machine states that warrant faster polling — device is mid-transition.
# Settled states: 0 (Standby), 1 (Normal). Transitional states per protocol doc;
# actual device behaviour during cleaning and cooldown must be validated on hardware.
_ACTIVE_MACHINE_STATES = {2, 3, 4, 5}  # Cooling Down, Overtemp Standby, Cleaning, Clean Complete

# Setpoint ranges used to infer whether the device is in Celsius or Fahrenheit mode.
# These ranges are non-overlapping so the unit can be determined unambiguously.
# Confirmed hardware range: 4–37°C / 40–99°F (firmware 3.26 and 3.8, 2026-04-02).
_CELSIUS_SETPOINT_MIN = 4
_CELSIUS_SETPOINT_MAX = 37
_FAHRENHEIT_SETPOINT_MIN = 40
_FAHRENHEIT_SETPOINT_MAX = 99

# Maximum plausible altitude per unit. Values above these indicate an
# uninitialized or garbage sensor reading and are reported as None.
# Bounds are deliberately generous (well above Everest) to avoid masking
# legitimate high-altitude use while still filtering device startup glitches.
_MAX_ALTITUDE_M = 9000   # metres — above Everest (8849 m)
_MAX_ALTITUDE_FT = 30000 # feet   — above Everest (29032 ft)

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


def _validate_altitude(raw: int, unit: str) -> int | None:
    """Return raw altitude if plausible, else None.

    Filters the 0xFFFF unavailable sentinel and values above a generous
    ceiling that no camping heater could realistically report. Startup glitches
    sometimes produce large non-sentinel values before the sensor initialises.
    """
    if raw == 0xFFFF:
        return None
    max_alt = _MAX_ALTITUDE_FT if unit == UnitOfTemperature.FAHRENHEIT else _MAX_ALTITUDE_M
    return raw if raw <= max_alt else None


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
        self._configured_interval = timedelta(seconds=poll_seconds)
        # Detected on first connect — preserved for the lifetime of the entry.
        self.temp_unit: str = UnitOfTemperature.CELSIUS
        # Prime pump state — owned by VelitHeaterFuelPrimingSwitch, read by the
        # countdown sensor. Stored here so both entities share it without coupling.
        self.priming: bool = False
        self.prime_remaining: int = 0
        self._prime_tick_callbacks: list = []
        # Cleaning state — managed by start_cleaning(); cleared here when the
        # device returns to Standby after having been active.
        self.cleaning: bool = False
        self._cleaning_seen_active: bool = False
        self._cleaning_timeout_polls: int = 0
        # Poll failure tolerance — see _async_update_data.
        self._consecutive_failures: int = 0
        self._last_data: dict | None = None
        # Fast polling window armed after any command. Holds 5s interval for N
        # cycles so state transitions are caught quickly without waiting 30s.
        self._post_command_fast_polls: int = 0

    def register_prime_tick(self, callback) -> None:
        """Register a callback to fire on each prime countdown tick."""
        self._prime_tick_callbacks.append(callback)

    def _notify_prime_tick(self) -> None:
        """Notify all registered prime tick listeners (e.g. countdown sensor)."""
        for cb in self._prime_tick_callbacks:
            cb()

    def start_cleaning(self) -> None:
        """Mark a cleaning cycle as initiated and reset confirmation tracking."""
        self.cleaning = True
        self._cleaning_seen_active = False
        self._cleaning_timeout_polls = 0

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

    def _adjust_poll_interval(self, machine_state: int) -> None:
        """Switch to fast polling during active state transitions, restore when settled.

        Also uses fast polling while a cleaning cycle is pending confirmation or
        while the post-command window is active, so state changes after any
        command are caught within seconds rather than 30s.

        Pass machine_state=0 for device types that have no transitional states (AC).
        """
        if self.cleaning or machine_state in _ACTIVE_MACHINE_STATES or self._post_command_fast_polls > 0:
            if self.update_interval != _ACTIVE_POLL_INTERVAL:
                self.update_interval = _ACTIVE_POLL_INTERVAL
            if self._post_command_fast_polls > 0:
                self._post_command_fast_polls -= 1
        elif self.update_interval != self._configured_interval:
            self.update_interval = self._configured_interval

    @abstractmethod
    async def _async_poll(self) -> dict:
        """Issue device queries and return a data dict. Raise UpdateFailed on error."""

    async def _async_update_data(self) -> dict:
        """Called by DataUpdateCoordinator on each poll cycle.

        Tolerates up to _POLL_FAILURE_TOLERANCE consecutive failures before
        marking entities unavailable. On a tolerated failure the last known
        data is returned so the UI stays stable across transient BLE timeouts.
        """
        try:
            data = await self._async_poll()
        except UpdateFailed as exc:
            self._consecutive_failures += 1
            if self._consecutive_failures < _POLL_FAILURE_TOLERANCE and self._last_data is not None:
                _LOGGER.debug(
                    "Poll failed (%d/%d), returning stale data: %s",
                    self._consecutive_failures,
                    _POLL_FAILURE_TOLERANCE,
                    exc,
                )
                return self._last_data
            raise
        except Exception as exc:
            self._consecutive_failures += 1
            if self._consecutive_failures < _POLL_FAILURE_TOLERANCE and self._last_data is not None:
                _LOGGER.debug(
                    "Poll failed (%d/%d), returning stale data: %s",
                    self._consecutive_failures,
                    _POLL_FAILURE_TOLERANCE,
                    exc,
                )
                return self._last_data
            raise UpdateFailed(f"Unexpected error polling {self._address}: {exc}") from exc

        self._consecutive_failures = 0
        self._last_data = data
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
        self._client = VelitHeaterClient(self.hass, self._address)
        self._unit_detected = False
        # Populated on first successful poll via func 0x6A. Exposed as sw_version
        # in DeviceInfo. None until queried; remains None if the query fails.
        self.firmware_version: str | None = None

    async def _async_query_firmware_version(self) -> None:
        """Query device firmware version via func 0x6A and cache the result.

        Response payload bytes [14:16] encode the version as a big-endian uint16
        decimal integer: e.g. 0x0139 = 313 → "3.13". Confirmed on firmware 3.13
        (single data point — encoding consistent with known decimal-integer convention).
        Logs a warning and leaves firmware_version as None on any failure.
        """
        try:
            rsp = await self._client.send_command(0x6A, bytes([0x01]))
            if rsp is None or len(rsp.get("data", b"")) < 16:
                _LOGGER.warning("Firmware version query (0x6A) returned no usable response")
                return
            val = int.from_bytes(rsp["data"][14:16], "big")
            self.firmware_version = f"{val // 100}.{val % 100:02d}"
            _LOGGER.debug("Heater %s: firmware version %s", self._address, self.firmware_version)
        except Exception as exc:
            _LOGGER.warning("Firmware version query (0x6A) failed: %s", exc)

    async def _async_update_data(self) -> dict:
        data = await super()._async_update_data()
        self._adjust_poll_interval(data.get("machine_state", 0))
        if self.firmware_version is None:
            await self._async_query_firmware_version()
        # Track cleaning cycle completion.
        # Only clear once the device has confirmed it left Standby (cycle started)
        # and then returned to Standby (cycle complete). If the device never leaves
        # Standby within the timeout window, the command was not accepted.
        if self.cleaning:
            machine_state = data.get("machine_state", 0)
            if machine_state != 0:
                self._cleaning_seen_active = True
                self._cleaning_timeout_polls = 0
            elif self._cleaning_seen_active:
                # Returned to Standby after being active — cycle complete.
                self.cleaning = False
                self._cleaning_seen_active = False
                self._cleaning_timeout_polls = 0
            else:
                # Still in Standby — waiting for device to transition.
                self._cleaning_timeout_polls += 1
                if self._cleaning_timeout_polls >= 6:
                    # ~30s at 5s poll rate with no transition — command not accepted.
                    _LOGGER.warning(
                        "Cleaning command not confirmed by device after %d polls — clearing",
                        self._cleaning_timeout_polls,
                    )
                    self.cleaning = False
                    self._cleaning_seen_active = False
                    self._cleaning_timeout_polls = 0
        return data

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

        # TEMP: remove after cleaning and cooldown state transitions confirmed on hardware
        _LOGGER.debug(
            "machine_state raw=%d (%s)",
            machine_state,
            HEATER_MACHINE_STATES.get(machine_state, f"Unknown ({machine_state})"),
        )

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
            "altitude": _validate_altitude(alt_raw, self.temp_unit),
        }


class VelitACCoordinator(_VelitBaseCoordinator):
    """Coordinator for Velit AC devices (protocol V1.01).

    Polls power (0x01), mode (0x02), temperature (0x03), fan speed (0x04),
    swing (0x10), inlet air temperature (0x07), outlet air temperature (0x08),
    supply voltage (0x12), amperage (0x13), and fault info (0x0B) on each cycle.

    Data dict keys:
      power             int     — 0x01 = off, 0x02 = on (func 0x01 response)
      mode              int     — current operation mode code (see AC protocol 0x02)
      set_temp_c        float   — current setpoint in Celsius
      fan_speed         int     — current fan speed 1–5
      inlet_temp_c      float | None  — inlet air temperature in Celsius (raw byte = °C)
      outlet_temp_c     float | None  — outlet air temperature in Celsius (int8)
      voltage_v         float | None  — supply voltage in volts (uint16 decivolts)
      amperage_a        float | None  — supply current; documented as ALWAYS 0 on
                                        these control boards, so the sensor built on
                                        this is disabled by default
      fault_code        int     — raw fault code (0 = no fault)
      fault_name        str     — human-readable fault description
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry, name=f"Velit AC {entry.data['address']}")
        self._client = VelitACClient(self.hass, self._address)
        self._unit_detected = False

    async def _async_update_data(self) -> dict:
        data = await super()._async_update_data()
        # AC has no machine state transitions — pass 0 so only the post-command
        # fast poll window drives the interval, not machine states.
        self._adjust_poll_interval(0)
        return data

    async def _async_poll(self) -> dict:
        power_rsp = await self._client.send_command(0x01, bytes([0x00]))
        if power_rsp is None:
            raise UpdateFailed("No response to power query (0x01)")

        mode_rsp = await self._client.send_command(0x02, bytes([0x00]))
        if mode_rsp is None:
            raise UpdateFailed("No response to mode query (0x02)")

        temp_rsp = await self._client.send_command(0x03, bytes([0x00]))
        if temp_rsp is None:
            raise UpdateFailed("No response to temperature query (0x03)")

        fan_rsp = await self._client.send_command(0x04, bytes([0x00]))
        if fan_rsp is None:
            raise UpdateFailed("No response to fan speed query (0x04)")

        # Inlet/outlet temp, voltage, amperage and fault: do not raise on failure —
        # the device may not respond to these while powered off.
        inlet_rsp = await self._client.send_command(0x07, bytes([0x00]))
        outlet_rsp = await self._client.send_command(0x08, bytes([0x00]))
        voltage_rsp = await self._client.send_command(0x12, bytes([0x00]))
        amperage_rsp = await self._client.send_command(0x13, bytes([0x00]))
        lcd_rsp = await self._client.send_command(0x0A, bytes([0x00]))
        fault_rsp = await self._client.send_command(0x0B, bytes([0x00]))

        set_temp_raw = temp_rsp["data"][0]
        if not self._unit_detected:
            self.temp_unit = self._detect_temp_unit(set_temp_raw)
            self._unit_detected = True
            _LOGGER.debug("AC %s: detected temp unit %s", self._address, self.temp_unit)

        # Inlet temp: single byte, always in Celsius. Confirmed on hardware (0x18 = 24°C).
        inlet_temp_c: float | None = None
        if inlet_rsp is not None and inlet_rsp["data"]:
            inlet_temp_c = float(inlet_rsp["data"][0])

        # Outlet temp: int8 Celsius per protocol key 8 (signed — can read below zero).
        outlet_temp_c: float | None = None
        if outlet_rsp is not None and outlet_rsp["data"]:
            raw = outlet_rsp["data"][0]
            outlet_temp_c = float(raw - 256 if raw > 127 else raw)

        def _u16_scaled(rsp: dict | None, scale: float, lo: float, hi: float) -> float | None:
            """Decode a uint16 field, resolving byte order by plausibility.

            The AC protocol (V1.01) documents keys 18/19 as uint16 but does not
            state endianness, and unlike the heater protocol there is no worked
            example to copy. Rather than guess, try big-endian first and fall
            back to little-endian only if big-endian lands outside a physically
            plausible range. Returns None if neither ordering is sensible.
            """
            if rsp is None or not rsp["data"]:
                return None
            d = rsp["data"]
            if len(d) == 1:
                candidates = [d[0]]
            else:
                candidates = [(d[0] << 8) | d[1], (d[1] << 8) | d[0]]
            for raw in candidates:
                value = raw * scale
                if lo <= value <= hi:
                    return round(value, 1)
            _LOGGER.debug(
                "AC %s: uint16 field out of plausible range, raw bytes %s",
                self._address, d.hex(),
            )
            return None

        # Voltage: uint16 decivolts. Range covers 12V/24V/48V systems with margin.
        voltage_v = _u16_scaled(voltage_rsp, 0.1, 5.0, 70.0)

        # Amperage: documented as ALWAYS 0 on these control boards (gongloo/OutEquipAC
        # protocol.md). Polled anyway so the disabled-by-default sensor can confirm
        # whether this particular firmware behaves differently. lo=0 accepts the 0.
        amperage_a = _u16_scaled(amperage_rsp, 0.1, 0.0, 300.0)

        # LCD (key 10): 0 = display on, 1 = display off, INVERTED relative to the
        # standard on/off encoding. When the unit is powered off the display is
        # physically off no matter what this reports, so the switch entity gates
        # on power state as well.
        lcd_raw: int | None = None
        if lcd_rsp is not None and lcd_rsp["data"]:
            lcd_raw = lcd_rsp["data"][0]

        # Fault: single byte, 0x00 = no fault. Code table not in protocol doc.
        fault_code = 0
        if fault_rsp is not None and fault_rsp["data"]:
            fault_code = fault_rsp["data"][0]
        fault_name = "No Fault" if fault_code == 0 else f"Unknown ({fault_code})"

        return {
            "power": power_rsp["data"][0],
            "mode": mode_rsp["data"][0],
            "set_temp_c": self.to_celsius(set_temp_raw),
            "fan_speed": fan_rsp["data"][0],
            "inlet_temp_c": inlet_temp_c,
            "outlet_temp_c": outlet_temp_c,
            "voltage_v": voltage_v,
            "amperage_a": amperage_a,
            "lcd_raw": lcd_raw,
            "fault_code": fault_code,
            "fault_name": fault_name,
        }
