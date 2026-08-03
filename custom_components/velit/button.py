"""Button entities for Velit heater devices.

No button entities are currently registered. Cleaning was a button in earlier
versions and is now a switch in switch.py to provide visible cycle state.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Velit button entities from a config entry."""
