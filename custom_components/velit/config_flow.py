"""Config flow for the Velit integration.

Two entry paths:
  - Bluetooth discovery: HA detects a Velit advertisement and calls
    async_step_bluetooth automatically.
  - Manual: user initiates from the UI and enters the BLE address.

Both paths converge at async_step_device_type where the user selects
Heater or AC and assigns a friendly name. Device type cannot be inferred
from the BLE advertisement — user selection is the safe fallback until
hardware verification confirms a reliable automatic detection method.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
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

from .const import CONF_POLL_INTERVAL, CONF_UNAVAILABLE_ON_FAULT, DEVICE_TYPE_AC, DEVICE_TYPE_HEATER, DOMAIN

_LOGGER = logging.getLogger(__name__)


class VelitConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Velit config flow."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return VelitOptionsFlow(config_entry)

    def __init__(self) -> None:
        self._address: str = ""
        self._name: str = ""

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
        """Handle manual entry — user provides the BLE device address."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            self._address = address
            self._name = address
            return await self.async_step_device_type()

        return self.async_show_form(
            step_id="user",
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
