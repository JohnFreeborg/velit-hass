"""Unit tests for the heater Error 16 self-heal (setpoint re-send on frozen readings).

All BLE client calls are mocked — no hardware required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import UnitOfTemperature

from custom_components.velit.const import (
    CONF_SELF_HEAL_ALT_DELTA,
    CONF_SELF_HEAL_ALTITUDE,
    CONF_SELF_HEAL_ENABLED,
    CONF_SELF_HEAL_HOURS,
    CONF_SELF_HEAL_REF_DELTA,
    CONF_SELF_HEAL_REF_ENTITY,
)
from custom_components.velit.coordinator import VelitHeaterCoordinator

_TIME = "custom_components.velit.coordinator.time.monotonic"
HOUR = 3600.0

# Standby, Fahrenheit setpoint 0x28 = 40 °F — the van's values during the 2026-10-21 episode.
Q1_STANDBY = bytes([0x00, 0x02, 0x03, 0x28, 0x00, 0x50, 0x00])
Q1_RUNNING = bytes([0x00, 0x02, 0x03, 0x28, 0x01, 0x50, 0x00])


def _q2(inlet=0x70, casing=0x4D, outlet=0x4C, alt=1029) -> bytes:
    return bytes([
        0x00,
        0x00, 0x7D,
        0x00, 0x00,
        inlet >> 8, inlet & 0xFF,
        casing >> 8, casing & 0xFF,
        outlet >> 8, outlet & 0xFF,
        alt >> 8, alt & 0xFF,
    ])


def _ref_state(value, unit=UnitOfTemperature.FAHRENHEIT):
    state = MagicMock()
    state.state = str(value)
    state.attributes = {"unit_of_measurement": unit}
    return state


class _Rig:
    """Coordinator plus a scripted device: each poll answers Q1/Q2, writes succeed."""

    def __init__(self, options: dict, ha_unit: str = UnitOfTemperature.FAHRENHEIT) -> None:
        hass = MagicMock()
        hass.config.units.temperature_unit = ha_unit
        hass.states.get = MagicMock(return_value=None)
        entry = MagicMock()
        entry.data = {"device_type": "heater", "address": "AA:BB:CC:DD:EE:FF", "name": "Test"}
        entry.options = {CONF_SELF_HEAL_ENABLED: True, **options}
        with patch("custom_components.velit.coordinator.VelitHeaterClient") as mock_cls:
            self.coord = VelitHeaterCoordinator(hass, entry)
        self.coord._client = mock_cls.return_value
        self.hass = hass
        self.writes: list[bytes] = []
        self.q1 = Q1_STANDBY
        self.q2 = _q2()

        async def send(func, payload):
            if func == 0x0A:
                return {"data": self.q1}
            if func == 0x0B:
                return {"data": self.q2}
            self.writes.append(bytes([func]) + payload)
            return {"data": payload}

        self.coord._client.send_command = AsyncMock(side_effect=send)

    async def poll(self, at: float) -> dict:
        with patch(_TIME, return_value=at):
            return await self.coord._async_poll()


class TestSelfHealTime:
    async def test_fires_after_window(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 3})
        await rig.poll(0)
        await rig.poll(2.9 * HOUR)
        assert rig.writes == []
        await rig.poll(3 * HOUR)
        assert rig.writes == [bytes([0x08, 0x28])]
        assert rig.coord.last_self_heal_trigger == "time"
        assert rig.coord.last_self_heal is not None
        assert rig.coord._post_command_fast_polls == 6

    async def test_temperature_change_restarts_window(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 3})
        await rig.poll(0)
        rig.q2 = _q2(inlet=0x71)
        await rig.poll(2 * HOUR)
        await rig.poll(4 * HOUR)
        assert rig.writes == []
        await rig.poll(5 * HOUR)
        assert len(rig.writes) == 1

    @pytest.mark.parametrize("field", ["casing", "outlet"])
    async def test_only_inlet_counts(self, field):
        # Casing and outlet stayed put when the inlet unfroze on 2026-10-21,
        # so they are not part of the freeze signature.
        rig = _Rig({CONF_SELF_HEAL_HOURS: 3})
        await rig.poll(0)
        rig.q2 = _q2(**{field: 0x50})
        await rig.poll(3 * HOUR)
        assert len(rig.writes) == 1

    async def test_not_in_standby_never_fires(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 1})
        rig.q1 = Q1_RUNNING
        await rig.poll(0)
        await rig.poll(5 * HOUR)
        assert rig.writes == []

    @pytest.mark.parametrize("gate", ["priming", "cleaning", "post_command"])
    async def test_gated_states_restart_window(self, gate):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 3})
        await rig.poll(0)
        if gate == "post_command":
            rig.coord._post_command_fast_polls = 3
        else:
            setattr(rig.coord, gate, True)
        await rig.poll(2 * HOUR)
        rig.coord.priming = rig.coord.cleaning = False
        rig.coord._post_command_fast_polls = 0
        await rig.poll(4 * HOUR)  # fresh baseline
        await rig.poll(6.9 * HOUR)
        assert rig.writes == []
        await rig.poll(7 * HOUR)
        assert len(rig.writes) == 1

    async def test_unavailable_reading_never_counts_as_frozen(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 1})
        rig.q2 = _q2(inlet=0xFFFF)
        await rig.poll(0)
        await rig.poll(5 * HOUR)
        assert rig.writes == []

    async def test_at_most_once_an_hour(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 1})
        await rig.poll(0)
        await rig.poll(1 * HOUR)
        assert len(rig.writes) == 1
        # The re-send opens the post-command window; once it closes the readings
        # are still frozen, so the next attempt waits for the window again.
        rig.coord._post_command_fast_polls = 0
        await rig.poll(1.5 * HOUR)
        await rig.poll(2.4 * HOUR)
        assert len(rig.writes) == 1
        await rig.poll(2.5 * HOUR)
        assert len(rig.writes) == 2

    async def test_disabled_does_nothing(self):
        rig = _Rig({CONF_SELF_HEAL_ENABLED: False, CONF_SELF_HEAL_HOURS: 1})
        await rig.poll(0)
        await rig.poll(5 * HOUR)
        assert rig.writes == []

    async def test_write_failure_is_logged_not_raised(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 1})
        original = rig.coord._client.send_command.side_effect

        async def failing(func, payload):
            if func == 0x08:
                raise TimeoutError("no echo")
            return await original(func, payload)

        rig.coord._client.send_command.side_effect = failing
        await rig.poll(0)
        data = await rig.poll(1 * HOUR)
        assert data["machine_state"] == 0
        assert rig.coord.last_self_heal is None


class TestSelfHealReferenceSensor:
    async def test_fires_on_reference_drift(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 24, CONF_SELF_HEAL_REF_ENTITY: "sensor.cabin"})
        rig.hass.states.get.return_value = _ref_state(80.0)
        await rig.poll(0)
        rig.hass.states.get.return_value = _ref_state(76.0)  # 2.2 C
        await rig.poll(0.2 * HOUR)
        assert rig.writes == []
        rig.hass.states.get.return_value = _ref_state(74.0)  # 3.3 C
        await rig.poll(0.3 * HOUR)
        assert rig.writes == [bytes([0x08, 0x28])]
        assert rig.coord.last_self_heal_trigger == "reference sensor"

    async def test_celsius_reference(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 24, CONF_SELF_HEAL_REF_ENTITY: "sensor.cabin"})
        rig.hass.states.get.return_value = _ref_state(20.0, UnitOfTemperature.CELSIUS)
        await rig.poll(0)
        rig.hass.states.get.return_value = _ref_state(23.5, UnitOfTemperature.CELSIUS)
        await rig.poll(0.1 * HOUR)
        assert len(rig.writes) == 1

    @pytest.mark.parametrize("state", ["unavailable", "unknown"])
    async def test_unusable_reference_is_skipped(self, state):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 24, CONF_SELF_HEAL_REF_ENTITY: "sensor.cabin"})
        rig.hass.states.get.return_value = _ref_state(80.0)
        await rig.poll(0)
        rig.hass.states.get.return_value = _ref_state(state)
        await rig.poll(1 * HOUR)
        assert rig.writes == []

    async def test_custom_deviation(self):
        rig = _Rig({
            CONF_SELF_HEAL_HOURS: 24,
            CONF_SELF_HEAL_REF_ENTITY: "sensor.cabin",
            CONF_SELF_HEAL_REF_DELTA: 2,
        })
        rig.hass.states.get.return_value = _ref_state(80.0)
        await rig.poll(0)
        rig.hass.states.get.return_value = _ref_state(78.5)
        await rig.poll(0.1 * HOUR)
        assert rig.writes == []
        rig.hass.states.get.return_value = _ref_state(78.0)
        await rig.poll(0.2 * HOUR)
        assert len(rig.writes) == 1

    async def test_metric_ha_deviation_in_celsius(self):
        rig = _Rig(
            {CONF_SELF_HEAL_HOURS: 24, CONF_SELF_HEAL_REF_ENTITY: "sensor.cabin"},
            ha_unit=UnitOfTemperature.CELSIUS,
        )
        rig.hass.states.get.return_value = _ref_state(20.0, UnitOfTemperature.CELSIUS)
        await rig.poll(0)
        rig.hass.states.get.return_value = _ref_state(22.5, UnitOfTemperature.CELSIUS)
        await rig.poll(0.1 * HOUR)
        assert rig.writes == []  # default 3 C
        rig.hass.states.get.return_value = _ref_state(23.0, UnitOfTemperature.CELSIUS)
        await rig.poll(0.2 * HOUR)
        assert len(rig.writes) == 1

    async def test_exact_deviation_with_decimal_readings(self):
        # As floats, 64.1 - 62.1 = 1.9999999999999929 and would miss a 2 °F threshold.
        rig = _Rig({
            CONF_SELF_HEAL_HOURS: 24,
            CONF_SELF_HEAL_REF_ENTITY: "sensor.cabin",
            CONF_SELF_HEAL_REF_DELTA: 2,
        })
        rig.hass.states.get.return_value = _ref_state(64.1)
        await rig.poll(0)
        rig.hass.states.get.return_value = _ref_state(62.1)
        await rig.poll(0.1 * HOUR)
        assert len(rig.writes) == 1

    async def test_exact_cross_unit_deviation(self):
        # HA in °F, reference reports °C: a 5 °C step is exactly 9 °F.
        rig = _Rig({
            CONF_SELF_HEAL_HOURS: 24,
            CONF_SELF_HEAL_REF_ENTITY: "sensor.cabin",
            CONF_SELF_HEAL_REF_DELTA: 9,
        })
        rig.hass.states.get.return_value = _ref_state(20.0, UnitOfTemperature.CELSIUS)
        await rig.poll(0)
        rig.hass.states.get.return_value = _ref_state(24.9, UnitOfTemperature.CELSIUS)
        await rig.poll(0.1 * HOUR)
        assert rig.writes == []
        rig.hass.states.get.return_value = _ref_state(25.0, UnitOfTemperature.CELSIUS)
        await rig.poll(0.2 * HOUR)
        assert len(rig.writes) == 1

    async def test_non_temperature_unit_reference_is_skipped(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 24, CONF_SELF_HEAL_REF_ENTITY: "sensor.cabin"})
        rig.hass.states.get.return_value = _ref_state(50.0, "%")
        await rig.poll(0)
        rig.hass.states.get.return_value = _ref_state(90.0, "%")
        await rig.poll(0.1 * HOUR)
        assert rig.writes == []

    async def test_reference_baseline_taken_when_it_first_reports(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 24, CONF_SELF_HEAL_REF_ENTITY: "sensor.cabin"})
        rig.hass.states.get.return_value = None
        await rig.poll(0)
        rig.hass.states.get.return_value = _ref_state(80.0)
        await rig.poll(0.1 * HOUR)
        assert rig.writes == []
        rig.hass.states.get.return_value = _ref_state(73.0)
        await rig.poll(0.2 * HOUR)
        assert len(rig.writes) == 1


class TestSelfHealAltitude:
    async def test_fires_on_altitude_range(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 24, CONF_SELF_HEAL_ALTITUDE: True})
        await rig.poll(0)
        rig.q2 = _q2(alt=1100)
        await rig.poll(0.1 * HOUR)
        assert rig.writes == []
        # Range counts, not the latest delta: 1100 high, now 1000 low.
        rig.q2 = _q2(alt=1000)
        await rig.poll(0.2 * HOUR)
        assert rig.writes == [bytes([0x08, 0x28])]
        assert rig.coord.last_self_heal_trigger == "altitude"

    async def test_parked_jitter_does_not_fire(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 24, CONF_SELF_HEAL_ALTITUDE: True})
        for i, alt in enumerate([1029, 1038, 1022, 1036, 1026]):
            rig.q2 = _q2(alt=alt)
            await rig.poll(i * 60.0)
        assert rig.writes == []

    async def test_altitude_ignored_when_option_off(self):
        rig = _Rig({CONF_SELF_HEAL_HOURS: 24})
        await rig.poll(0)
        rig.q2 = _q2(alt=1500)
        await rig.poll(0.1 * HOUR)
        assert rig.writes == []

    async def test_custom_altitude_change(self):
        rig = _Rig({
            CONF_SELF_HEAL_HOURS: 24,
            CONF_SELF_HEAL_ALTITUDE: True,
            CONF_SELF_HEAL_ALT_DELTA: 500,
        })
        await rig.poll(0)
        rig.q2 = _q2(alt=1029 + 424)  # today's drive span
        await rig.poll(0.1 * HOUR)
        assert rig.writes == []
        rig.q2 = _q2(alt=1029 + 500)
        await rig.poll(0.2 * HOUR)
        assert len(rig.writes) == 1

    async def test_metric_ha_altitude_converted_to_device_feet(self):
        # HA in metres (30 m default = 98.43 ft); the device here reports feet.
        rig = _Rig(
            {CONF_SELF_HEAL_HOURS: 24, CONF_SELF_HEAL_ALTITUDE: True},
            ha_unit=UnitOfTemperature.CELSIUS,
        )
        await rig.poll(0)
        rig.q2 = _q2(alt=1029 + 98)
        await rig.poll(0.1 * HOUR)
        assert rig.writes == []
        rig.q2 = _q2(alt=1029 + 99)
        await rig.poll(0.2 * HOUR)
        assert len(rig.writes) == 1
