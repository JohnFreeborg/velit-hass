"""Unit tests for heater and AC packet builders and parsers.

Test vectors are taken directly from the Velit protocol documents
(Heater V1.02, AC V1.01) and used as ground truth.

Where a protocol document example does not match the documented checksum
formula, this is noted explicitly. These cases require hardware verification
to determine whether the document contains a typo or the formula description
is incomplete.
"""

from __future__ import annotations

import pytest

from custom_components.velit.ac_client import build_command as ac_build
from custom_components.velit.ac_client import parse_response as ac_parse
from custom_components.velit.heater_client import build_command as heater_build
from custom_components.velit.heater_client import parse_response as heater_parse
from custom_components.velit.packet_utils import (
    celsius_to_fahrenheit,
    fahrenheit_to_celsius,
    hex_to_celsius,
    hex_to_fahrenheit,
    is_valid_ac_temp_c,
    is_valid_ac_temp_f,
    is_valid_heater_temp_c,
    is_valid_heater_temp_f,
)

# Example addresses used throughout the protocol document.
MASTER = bytes([0x00, 0x00, 0x00, 0x01])
SLAVE = bytes([0x00, 0x00, 0x00, 0x2D])


# ---------------------------------------------------------------------------
# Heater command packet tests
# ---------------------------------------------------------------------------


class TestHeaterBuildCommand:
    """Verify heater command packets against protocol document examples."""

    def _cmd(self, func: int, data: int) -> bytes:
        return heater_build(MASTER, SLAVE, func, bytes([data]))

    def test_start_flag(self):
        assert self._cmd(0x01, 0x01)[0] == 0x55

    def test_length_field(self):
        # Length = master(4) + slave(4) + func(1) + data(1) + checksum(2) = 12 = 0x0C
        assert self._cmd(0x01, 0x01)[1] == 0x0C

    def test_mode_switch_manual(self):
        # 55 0C 00000001 0000002D 00 01 00 90
        pkt = self._cmd(0x00, 0x01)
        assert pkt[-2:] == bytes([0x00, 0x90])

    def test_mode_switch_thermostat(self):
        # 55 0C 00000001 0000002D 00 02 00 91
        pkt = self._cmd(0x00, 0x02)
        assert pkt[-2:] == bytes([0x00, 0x91])

    def test_startup_manual(self):
        # 55 0C 00000001 0000002D 01 01 00 91
        pkt = self._cmd(0x01, 0x01)
        assert pkt[-2:] == bytes([0x00, 0x91])

    def test_start_ventilation(self):
        # 55 0C 00000001 0000002D 03 00 00 92
        pkt = self._cmd(0x03, 0x00)
        assert pkt[-2:] == bytes([0x00, 0x92])

    def test_stop_ventilation(self):
        # 55 0C 00000001 0000002D 04 00 00 93
        pkt = self._cmd(0x04, 0x00)
        assert pkt[-2:] == bytes([0x00, 0x93])

    def test_start_fuel_pump(self):
        # 55 0C 00000001 0000002D 05 00 00 94
        pkt = self._cmd(0x05, 0x00)
        assert pkt[-2:] == bytes([0x00, 0x94])

    def test_stop_fuel_pump(self):
        # 55 0C 00000001 0000002D 06 00 00 95
        pkt = self._cmd(0x06, 0x00)
        assert pkt[-2:] == bytes([0x00, 0x95])

    def test_set_gear_3(self):
        # 55 0C 00000001 0000002D 07 03 00 99
        pkt = self._cmd(0x07, 0x03)
        assert pkt[-2:] == bytes([0x00, 0x99])

    def test_set_temperature_80f(self):
        # 55 0C 00000001 0000002D 08 50 00 E7 (0x50 = 80°F)
        pkt = self._cmd(0x08, 0x50)
        assert pkt[-2:] == bytes([0x00, 0xE7])

    def test_residual_fuel_clearance(self):
        # 55 0C 00000001 0000002D 09 00 00 98
        pkt = self._cmd(0x09, 0x00)
        assert pkt[-2:] == bytes([0x00, 0x98])

    def test_query_timer(self):
        # 55 0C 00000001 0000002D 0F 00 00 9E
        pkt = self._cmd(0x0F, 0x00)
        assert pkt[-2:] == bytes([0x00, 0x9E])

    def test_shutdown_document_discrepancy(self):
        """The protocol document shows checksum 00 85 for the shutdown command.

        The documented formula (sum of all bytes from start flag through data)
        produces 00 91 for these inputs. This discrepancy is suspected to be
        a typo in the source document. This test records the formula result
        and flags the disagreement for hardware verification.

        See: Velit Heater Communication Protocol V1.02, Command Table 2.
        """
        pkt = self._cmd(0x02, 0x00)
        # Formula gives 00 91; document shows 00 85.
        # Using formula result — hardware test required to confirm.
        assert pkt[-2:] == bytes([0x00, 0x91]), (
            "Checksum disagrees with document example (00 85). "
            "Suspected document typo — verify against hardware."
        )

    def test_invalid_address_length(self):
        with pytest.raises(ValueError):
            heater_build(bytes([0x01]), SLAVE, 0x01, bytes([0x01]))

    def test_address_embedded_correctly(self):
        pkt = self._cmd(0x01, 0x01)
        assert pkt[2:6] == MASTER
        assert pkt[6:10] == SLAVE


# ---------------------------------------------------------------------------
# Heater response parser tests
# ---------------------------------------------------------------------------


class TestHeaterParseResponse:
    """Verify heater response parsing against protocol document examples."""

    def _rsp(self, func: int, data: int) -> bytes:
        """Build a valid heater response using the known formula."""
        base = (
            bytes([0xAA, 0x0E])
            + MASTER
            + SLAVE
            + bytes([0x53, 0x46, func, data])
        )
        total = sum(base) & 0xFFFF
        return base + bytes([total >> 8, total & 0xFF])

    def test_startup_response(self):
        # AA 0E 00000001 0000002D 5346 01 01 01 81
        result = heater_parse(self._rsp(0x01, 0x01))
        assert result is not None
        assert result["func"] == 0x01
        assert result["data"] == bytes([0x01])

    def test_start_ventilation_response(self):
        result = heater_parse(self._rsp(0x03, 0x01))
        assert result is not None
        assert result["func"] == 0x03

    def test_set_gear_response(self):
        # Data echoes the gear value
        result = heater_parse(self._rsp(0x07, 0x03))
        assert result is not None
        assert result["data"] == bytes([0x03])

    def test_addresses_returned(self):
        result = heater_parse(self._rsp(0x01, 0x01))
        assert result["master_addr"] == MASTER
        assert result["slave_addr"] == SLAVE

    def test_invalid_start_byte(self):
        raw = self._rsp(0x01, 0x01)
        raw = bytes([0x55]) + raw[1:]  # wrong start byte
        assert heater_parse(raw) is None

    def test_bad_manufacturer_code(self):
        raw = bytearray(self._rsp(0x01, 0x01))
        raw[10] = 0xFF  # corrupt mfg code
        assert heater_parse(bytes(raw)) is None

    def test_bad_checksum(self):
        raw = bytearray(self._rsp(0x01, 0x01))
        raw[-1] ^= 0xFF  # flip bits in checksum
        assert heater_parse(bytes(raw)) is None

    def test_too_short(self):
        assert heater_parse(bytes([0xAA, 0x01])) is None


# ---------------------------------------------------------------------------
# AC command packet tests
# ---------------------------------------------------------------------------


class TestACBuildCommand:
    """Verify AC command packets against protocol document examples."""

    def test_known_example(self):
        # Protocol example: 5A5A 06 01 01 01 BD 0D0A
        pkt = ac_build(func=0x01, data=bytes([0x01]), product_code=0x01)
        assert pkt == bytes([0x5A, 0x5A, 0x06, 0x01, 0x01, 0x01, 0xBD, 0x0D, 0x0A])

    def test_header(self):
        pkt = ac_build(func=0x01, data=bytes([0x01]))
        assert pkt[:2] == bytes([0x5A, 0x5A])

    def test_terminator(self):
        pkt = ac_build(func=0x01, data=bytes([0x01]))
        assert pkt[-2:] == bytes([0x0D, 0x0A])

    def test_checksum_position(self):
        # Checksum is the byte immediately before the terminator
        pkt = ac_build(func=0x01, data=bytes([0x01]))
        assert pkt[-3] == 0xBD

    def test_power_off(self):
        # Func 0x01, data 0x01 = power off
        pkt = ac_build(func=0x01, data=bytes([0x01]))
        assert pkt[4] == 0x01  # func
        assert pkt[5] == 0x01  # data

    def test_power_on(self):
        # Func 0x01, data 0x02 = power on
        pkt = ac_build(func=0x01, data=bytes([0x02]))
        assert pkt[5] == 0x02


# ---------------------------------------------------------------------------
# AC response parser tests
# ---------------------------------------------------------------------------


class TestACParseResponse:
    """Verify AC response parsing."""

    def _rsp(self, func: int, data: int, product_code: int = 0x01) -> bytes:
        """Build a valid AC response packet."""
        payload = bytes([0x5A, 0x5A, 0x06, product_code, func, data])
        checksum = sum(payload) & 0xFF
        return payload + bytes([checksum, 0x0D, 0x0A])

    def test_known_example(self):
        # Response mirrors the command example from the protocol doc
        raw = bytes([0x5A, 0x5A, 0x06, 0x01, 0x01, 0x01, 0xBD, 0x0D, 0x0A])
        result = ac_parse(raw)
        assert result is not None
        assert result["func"] == 0x01
        assert result["data"] == bytes([0x01])

    def test_func_extracted(self):
        result = ac_parse(self._rsp(0x02, 0x01))
        assert result["func"] == 0x02

    def test_data_extracted(self):
        result = ac_parse(self._rsp(0x03, 0x19))  # 0x19 = 25°C
        assert result["data"] == bytes([0x19])

    def test_product_code_extracted(self):
        result = ac_parse(self._rsp(0x01, 0x01, product_code=0x01))
        assert result["product_code"] == 0x01

    def test_bad_header(self):
        raw = self._rsp(0x01, 0x01)
        assert ac_parse(bytes([0x55]) + raw[1:]) is None

    def test_bad_terminator(self):
        raw = bytearray(self._rsp(0x01, 0x01))
        raw[-1] = 0xFF
        assert ac_parse(bytes(raw)) is None

    def test_bad_checksum(self):
        raw = bytearray(self._rsp(0x01, 0x01))
        raw[-3] ^= 0xFF
        assert ac_parse(bytes(raw)) is None

    def test_too_short(self):
        assert ac_parse(bytes([0x5A, 0x5A])) is None


# ---------------------------------------------------------------------------
# Temperature utility tests
# ---------------------------------------------------------------------------


class TestTemperatureUtils:
    """Verify temperature conversion and validation helpers."""

    def test_celsius_to_fahrenheit(self):
        # F = 1.8 * C + 32 (protocol formula)
        assert celsius_to_fahrenheit(4) == pytest.approx(39.2)
        assert celsius_to_fahrenheit(37) == pytest.approx(98.6)

    def test_fahrenheit_to_celsius(self):
        assert fahrenheit_to_celsius(98.6) == pytest.approx(37.0)
        assert fahrenheit_to_celsius(39.2) == pytest.approx(4.0)

    def test_heater_valid_celsius_range(self):
        assert is_valid_heater_temp_c(4) is True
        assert is_valid_heater_temp_c(37) is True
        assert is_valid_heater_temp_c(3) is False
        assert is_valid_heater_temp_c(38) is False

    def test_heater_valid_fahrenheit_range(self):
        assert is_valid_heater_temp_f(40) is True
        assert is_valid_heater_temp_f(99) is True
        assert is_valid_heater_temp_f(39) is False
        assert is_valid_heater_temp_f(100) is False

    def test_hex_to_fahrenheit(self):
        # Offset encoding verified on hardware (2026-03-25): raw - 60 = °F.
        # Inlet sensor returned 0x0083=131; 131-60=71°F confirmed.
        assert hex_to_fahrenheit(131) == 71
        assert hex_to_fahrenheit(60) == 0    # lower bound of protocol range

    def test_hex_to_celsius(self):
        # Offset encoding per protocol doc: raw - 50 = °C.
        assert hex_to_celsius(50) == 0       # 0°C
        assert hex_to_celsius(71) == 21      # 21°C (≈ 71°F, expected room temp)
        assert hex_to_celsius(250) == 200    # upper bound of protocol range

    def test_ac_valid_celsius_range(self):
        assert is_valid_ac_temp_c(17) is True
        assert is_valid_ac_temp_c(30) is True
        assert is_valid_ac_temp_c(16) is False
        assert is_valid_ac_temp_c(31) is False

    def test_ac_valid_fahrenheit_range(self):
        # AC minimum is 17°C = 62.6°F → 63°F integer minimum.
        assert is_valid_ac_temp_f(63) is True
        assert is_valid_ac_temp_f(86) is True
        assert is_valid_ac_temp_f(62) is False
        assert is_valid_ac_temp_f(87) is False
