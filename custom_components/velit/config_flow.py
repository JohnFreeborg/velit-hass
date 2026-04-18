"""Config flow for the Velit integration.

Two entry paths:
  - Bluetooth discovery: HA detects a Velit advertisement and calls
    async_step_bluetooth automatically.
  - Manual: user initiates from the UI; HA scans for visible Velit devices
    and presents a picker. If none are found the user is prompted to close
    the Velit mobile app (which holds the BLE connection) and retry, or
    fall back to entering the address manually.

Both paths converge at async_step_device_type where the user selects
Heater or AC and assigns a friendly name. Device type cannot be inferred
from the BLE advertisement — user selection is the safe fallback until
hardware verification confirms a reliable automatic detection method.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from .const import CONF_POLL_INTERVAL, CONF_UNAVAILABLE_ON_FAULT, DEVICE_TYPE_AC, DEVICE_TYPE_HEATER, DOMAIN, UUID_SERVICE

_LOGGER = logging.getLogger(__name__)

# BLE advertisement name prefixes that identify Velit devices, matching the
# manifest.json bluetooth matchers.
_VELIT_NAME_PREFIXES = ("VELIT", "VLIT", "D30", "KT")

# BEKEN Corp manufacturer ID (0x585A = 22618), present in all known Velit
# advertisements. Some firmware versions advertise with the MAC as the local
# name rather than a VELIT* prefix — manufacturer ID is the reliable fallback.
_VELIT_MANUFACTURER_ID = 22618


class VelitConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Velit config flow."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return VelitOptionsFlow(config_entry)

    def __init__(self) -> None:
        self._address: str = ""
        self._name: str = ""
        # Devices visible during the last scan, keyed by BLE address.
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    # ------------------------------------------------------------------
    # Bluetooth discovery path
    # ------------------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered via Bluetooth.

        Called automatically by HA when an advertisement matches a
        manifest bluetooth matcher. Sets unique ID from the BLE address
        and aborts if this device is already configured.
        """
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._address = discovery_info.address
        self._name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": self._name}

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a confirmation form before proceeding to device type selection."""
        if user_input is not None:
            return await self.async_step_device_type()

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._name},
        )

    # ------------------------------------------------------------------
    # Manual entry path
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Scan for visible Velit devices and present a picker.

        If no devices are found the user is shown a menu that lets them
        retry the scan (after closing the mobile app) or enter an address
        manually.
        """
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            self._address = address
            info = self._discovered.get(address)
            self._name = info.name if info and info.name else address
            return await self.async_step_device_type()

        self._discovered = {
            info.address: info
            for info in async_discovered_service_info(self.hass, connectable=True)
            if (info.name and info.name.startswith(_VELIT_NAME_PREFIXES))
            or _VELIT_MANUFACTURER_ID in info.manufacturer_data
            or UUID_SERVICE in info.service_uuids
        }

        if not self._discovered:
            return await self.async_step_not_found()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=addr,
                                    label=f"{info.name} ({addr})",
                                )
                                for addr, info in self._discovered.items()
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_not_found(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the not-found menu with mobile app guidance and retry/manual options."""
        return self.async_show_menu(
            step_id="not_found",
            menu_options=["retry", "manual"],
        )

    async def async_step_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-run the device scan after the user closes the mobile app."""
        return await self.async_step_user()

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual address entry fallback when BT scan finds no devices."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            self._address = address
            self._name = address
            return await self.async_step_device_type()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
        )

    # ------------------------------------------------------------------
    # Shared step — device type and name
    # ------------------------------------------------------------------

    async def async_step_device_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select device type (Heater / AC) and assign a friendly name.

        Device type cannot be reliably inferred from the BLE advertisement
        at this time — user selection is required.
        """
        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    CONF_ADDRESS: self._address,
                    "device_type": user_input["device_type"],
                    CONF_NAME: user_input[CONF_NAME],
                },
            )

        return self.async_show_form(
            step_id="device_type",
            data_schema=vol.Schema(
                {
                    vol.Required("device_type"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=DEVICE_TYPE_HEATER, label="Heater"
                                ),
                                SelectOptionDict(
                                    value=DEVICE_TYPE_AC, label="Air Conditioner"
                                ),
                            ]
                        )
                    ),
                    vol.Required(CONF_NAME, default=self._name): TextSelector(),
                }
            ),
        )


class VelitOptionsFlow(OptionsFlow):
    """Handle Velit integration options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self._config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=current.get(CONF_POLL_INTERVAL, 30),
                    ): NumberSelector(NumberSelectorConfig(
                        min=5, max=300, step=1, mode=NumberSelectorMode.BOX,
                    )),
                    vol.Required(
                        CONF_UNAVAILABLE_ON_FAULT,
                        default=current.get(CONF_UNAVAILABLE_ON_FAULT, False),
                    ): BooleanSelector(),
                }
            ),
        )
