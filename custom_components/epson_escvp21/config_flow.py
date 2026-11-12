"""Config flow for the Epson ESC/VP21 integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .client import (
    CommandRejected,
    ConnectionFailed,
    EscVp21Client,
    ResponseTimeout,
)
from .compat import schema as sch
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    KEY_POWER,
    KEY_SERIAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import EpsonConfigEntry

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = sch.Schema(
    {
        sch.Required(CONF_HOST): str,
        sch.Required(CONF_PORT, default=DEFAULT_PORT): sch.All(
            sch.Coerce(int), sch.Range(min=1, max=65535)
        ),
        sch.Required(CONF_NAME, default=DEFAULT_NAME): str,
    }
)


async def async_probe(host: str, port: int) -> str | None:
    """Check the projector answers PWR?; return its serial number if it gives one.

    The 6030UB answers SNO? in standby too, but other models may only answer
    while on, so a missing serial is not an error.
    Raises ConnectionFailed / ResponseTimeout / CommandRejected.
    """
    client = EscVp21Client(host, port, backoff_min=0, backoff_max=0)
    try:
        await client.query(KEY_POWER)
        try:
            return await client.query(KEY_SERIAL)
        except (CommandRejected, ResponseTimeout):
            return None
    finally:
        await client.close()


class EpsonEscVp21ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for host, port and name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})
            try:
                serial = await async_probe(host, port)
            except ConnectionFailed:
                errors["base"] = "cannot_connect"
            except (ResponseTimeout, CommandRejected):
                errors["base"] = "no_response"
            except Exception:
                _LOGGER.exception("Unexpected error probing %s:%s", host, port)
                errors["base"] = "unknown"
            else:
                # Without SNO (projector off) fall back to host:port; the
                # coordinator replaces it with the serial once it is on.
                await self.async_set_unique_id(serial or f"{host}:{port}")
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: host, CONF_PORT: port}
                )
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_HOST: host, CONF_PORT: port},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: EpsonConfigEntry) -> EpsonOptionsFlow:
        """Return the options flow."""
        return EpsonOptionsFlow()


class EpsonOptionsFlow(OptionsFlowWithReload):
    """Poll interval option."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        schema = sch.Schema(
            {
                sch.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
