"""Actions for the Epson ESC/VP21 integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError

from .client import EscVp21Error
from .compat import schema as sch
from .const import ATTR_COMMAND, ATTR_TIMEOUT, DOMAIN, SERVICE_SEND_COMMAND
from .coordinator import EpsonConfigEntry

ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SEND_COMMAND_SCHEMA = sch.Schema(
    {
        sch.Optional(ATTR_CONFIG_ENTRY_ID): str,
        sch.Required(ATTR_COMMAND): sch.All(str, sch.Length(min=1, max=64)),
        sch.Optional(ATTR_TIMEOUT): sch.All(
            sch.Coerce(float), sch.Range(min=0.5, max=60)
        ),
    }
)


def _get_entry(hass: HomeAssistant, entry_id: str | None) -> EpsonConfigEntry:
    loaded = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id is not None:
        loaded = [entry for entry in loaded if entry.entry_id == entry_id]
        if not loaded:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
    elif len(loaded) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_required"
        )
    return loaded[0]


async def _async_send_command(call: ServiceCall) -> ServiceResponse:
    entry = _get_entry(call.hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
    coordinator = entry.runtime_data
    command: str = call.data[ATTR_COMMAND].strip()
    try:
        response = await coordinator.client.send_raw(
            command, call.data.get(ATTR_TIMEOUT)
        )
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_command",
            translation_placeholders={"command": command},
        ) from err
    except EscVp21Error as err:
        response = f"error: {err}"
    if not command.endswith("?"):
        await coordinator.async_request_refresh()
    return {"command": command, "response": response}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        _async_send_command,
        schema=SEND_COMMAND_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
