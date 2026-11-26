"""Checked command sending for user-initiated device writes.

Both clients return None from send_command when a write fails, times out, or the
connection drops. Entity actions must not report success in that case, or the UI
would show a state (for example heater off) that the device never received.
"""

from __future__ import annotations

from typing import Any, Protocol

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


class _CommandClient(Protocol):
    async def send_command(self, func: int, data: bytes) -> dict | None: ...


async def async_send_checked(
    client: _CommandClient, func: int, data: bytes
) -> dict[str, Any]:
    """Send a command and raise HomeAssistantError if the device did not reply."""
    response = await client.send_command(func, data)
    if response is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
        )
    return response
