"""Shared packet utilities for Velit heater and AC protocols.

Temperature handling strategy:
  The integration preserves whatever temperature unit is currently active on
  the device, to avoid changing what is shown on the physical LCD display.

  On connect the coordinator sends Query 1 (func 0x0A) and infers the active
  unit from the Set Temperature field in the response:
    - Value 16–30  → device is in Celsius mode
    - Value 61–86  → device is in Fahrenheit mode
    - Anything else → fall back to hass.config.units.temperature_unit

  All subsequent SET commands and sensor decodings use that unit. The
  coordinator converts to Celsius before reporting to HA so that HA can
  apply display conversion for users who prefer the other unit.

Two distinct temperature encodings exist in the protocol:

  SET commands (func 0x08):
    The data byte is the raw integer temperature value — no offset.
    celsius_to_hex / fahrenheit_to_hex are identity functions by design.

  QUERY responses (Query 2, func 0x0B):
    Sensor readings use an offset encoding verified on hardware:
      Celsius mode:    raw - 50 = °C   (range 0–250 maps to -50°C–200°C)
      Fahrenheit mode: raw - 60 = °F   (range 0–700 maps to -60°F–640°F)
    hex_to_celsius / hex_to_fahrenheit apply these offsets.
"""

from __future__ import annotations

from .const import (
    AC_MAX_TEMP_C,
    AC_MIN_TEMP_C,
    HEATER_MAX_TEMP_C,
    HEATER_MIN_TEMP_C,
)


def celsius_to_hex(temp_c: int) -> int:
    """Return the byte value to transmit for a Celsius SET command.

    The protocol transmits the raw integer degree value — no offset applied.
    """
    return temp_c


def fahrenheit_to_hex(temp_f: int) -> int:
    """Return the byte value to transmit for a Fahrenheit SET command.

    The protocol transmits the raw integer degree value — no offset applied.
    Retained for completeness; the integration operates in Celsius mode.
    """
    return temp_f


def hex_to_celsius(value: int) -> int:
    """Decode a raw sensor temperature value (from Query 2) to Celsius.

    Applies the protocol offset: raw - 50 = °C.
    Verified on hardware: device in Celsius mode.
    """
    return value - 50


def hex_to_fahrenheit(value: int) -> int:
    """Decode a raw sensor temperature value (from Query 2) to Fahrenheit.

    Applies the protocol offset: raw - 60 = °F.
    Verified on hardware (2026-03-25): inlet 0x0083=131, 131-60=71°F confirmed.
    Retained for completeness; the integration operates in Celsius mode.
    """
    return value - 60


def celsius_to_fahrenheit(temp_c: float) -> float:
    """Convert Celsius to Fahrenheit using the protocol formula."""
    return 1.8 * temp_c + 32


def fahrenheit_to_celsius(temp_f: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (temp_f - 32) / 1.8


def is_valid_heater_temp_c(temp_c: int) -> bool:
    """Return True if the temperature is within the heater's Celsius range."""
    return HEATER_MIN_TEMP_C <= temp_c <= HEATER_MAX_TEMP_C


def is_valid_heater_temp_f(temp_f: int) -> bool:
    """Return True if the temperature is within the heater's Fahrenheit range.

    F range: 61–86°F (16–30°C converted). Hex: 0x3D–0x56.
    Retained for completeness; the integration operates in Celsius mode.
    """
    return 61 <= temp_f <= 86


def is_valid_ac_temp_c(temp_c: int) -> bool:
    """Return True if the temperature is within the AC's Celsius range."""
    return AC_MIN_TEMP_C <= temp_c <= AC_MAX_TEMP_C


def is_valid_ac_temp_f(temp_f: int) -> bool:
    """Return True if the temperature is within the AC's Fahrenheit range.

    F range: 63–86°F (17–30°C converted: 17°C = 62.6°F → 63°F minimum).
    Retained for completeness; the integration operates in Celsius mode.
    """
    return 63 <= temp_f <= 86
