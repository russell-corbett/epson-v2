"""Diagnostics for the Epson ESC/VP21 integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .coordinator import EpsonConfigEntry

TO_REDACT = {CONF_HOST, "serial", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EpsonConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return async_redact_data(
        {
            "entry": {
                "data": dict(entry.data),
                "options": dict(entry.options),
                "unique_id": entry.unique_id,
            },
            "state": asdict(coordinator.data) if coordinator.data else None,
            "seen_codes": {
                key: sorted(codes) for key, codes in coordinator.seen_codes.items()
            },
            "last_update_success": coordinator.last_update_success,
            "consecutive_failures": coordinator.consecutive_failures,
            "client": {
                "connected": coordinator.client.connected,
                "stats": coordinator.client.stats.as_dict(),
            },
        },
        TO_REDACT,
    )
