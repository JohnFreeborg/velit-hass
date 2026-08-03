"""Velit heater and air conditioner integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import DEVICE_TYPE_HEATER
from .coordinator import VelitACCoordinator, VelitHeaterCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.CLIMATE, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Velit device from a config entry.

    Creates the appropriate coordinator, connects to the device, runs the
    first data refresh, and forwards setup to all platforms.

    Raises ConfigEntryNotReady if the device cannot be reached on startup —
    HA will retry automatically with backoff.
    """
    if entry.data["device_type"] == DEVICE_TYPE_HEATER:
        coordinator = VelitHeaterCoordinator(hass, entry)
        _remove_stale_entities(hass, entry)
    else:
        coordinator = VelitACCoordinator(hass, entry)

    try:
        await coordinator.async_connect()
    except Exception as exc:
        raise ConfigEntryNotReady(
            f"Could not connect to Velit device at {entry.data['address']}: {exc}"
        ) from exc

    # Register disconnect before first refresh so it runs even if refresh fails.
    entry.async_on_unload(coordinator.async_disconnect)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry when the user changes options so the coordinator picks
    # up the new poll interval and the climate entity re-evaluates availability.
    entry.async_on_unload(entry.add_update_listener(
        lambda h, e: h.config_entries.async_reload(e.entry_id)
    ))

    return True


def _remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove entity registry entries that no longer exist in the integration.

    Called on every setup so upgrades from older versions clean up automatically.
    Add entries here whenever an entity is removed or renamed between releases.
    """
    registry = er.async_get(hass)
    address = entry.data["address"]

    # Fuel pump prime was a button in earlier versions; replaced by a switch.
    stale = registry.async_get_entity_id("button", entry.domain, f"{address}_prime_fuel_pump")
    if stale:
        registry.async_remove(stale)
        _LOGGER.debug("Removed stale entity %s from registry", stale)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Velit config entry.

    Disconnect is handled automatically by the callback registered in
    async_setup_entry via entry.async_on_unload — no manual cleanup needed here.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
