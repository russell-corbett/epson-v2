"""Polling coordinator for the Epson ESC/VP21 integration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import (
    CommandRejected,
    ConnectionFailed,
    EscVp21Client,
    EscVp21Error,
    ResponseTimeout,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    KEY_ASPECT,
    KEY_CMODE,
    KEY_ERROR,
    KEY_LAMP,
    KEY_LUMINANCE,
    KEY_POWER,
    KEY_SERIAL,
    KEY_SOURCE,
    LAMP_POLL_INTERVAL,
    LAMP_POLL_INTERVAL_OFF,
    MAX_CONSECUTIVE_FAILURES,
    PWR_BUSY,
    PWR_ON,
)

_LOGGER = logging.getLogger(__name__)

# Queried on every cycle while the projector is on.
ON_KEYS = (KEY_SOURCE, KEY_CMODE, KEY_LUMINANCE, KEY_ASPECT, KEY_ERROR)
# Keys whose learned codes are remembered for the select entities.
SELECT_KEYS = frozenset({KEY_SOURCE, KEY_CMODE, KEY_LUMINANCE, KEY_ASPECT})
# A rejection never clears these; the last known value is kept.
STICKY_KEYS = frozenset({KEY_LAMP, KEY_ERROR})

CODES_STORE_VERSION = 1
CODES_SAVE_DELAY = 10  # seconds


def codes_store_key(entry_id: str) -> str:
    """Storage key for an entry's learned codes."""
    return f"{DOMAIN}.{entry_id}.codes"

type EpsonConfigEntry = ConfigEntry[EpsonCoordinator]


@dataclass
class ProjectorData:
    """Last known projector state. ``None`` means not (yet) known."""

    power: str | None = None
    values: dict[str, str | None] = field(default_factory=dict)
    lamp_hours: int | None = None
    serial: str | None = None

    @property
    def is_on(self) -> bool:
        """Return True if the projector reports PWR=01."""
        return self.power == PWR_ON

    def get(self, key: str) -> str | None:
        """Return the last value of an ESC/VP21 key."""
        return self.values.get(key)


class EpsonCoordinator(DataUpdateCoordinator[ProjectorData]):
    """Polls the projector and owns the single client connection."""

    config_entry: EpsonConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: EpsonConfigEntry, client: EscVp21Client
    ) -> None:
        """Initialize the coordinator."""
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host}:{client.port}",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.consecutive_failures = 0
        # Codes seen per select key, persisted so unnamed codes stay selectable.
        self.seen_codes: dict[str, set[str]] = {}
        self._last_poll: dict[str, datetime] = {}
        self._codes_store: Store[dict[str, dict[str, list[str]]]] = Store(
            hass, CODES_STORE_VERSION, codes_store_key(entry.entry_id)
        )
        self._rejections: dict[str, int] = {}
        # Some models only answer SNO? while on; retry it after power-on.
        self._serial_rejected = False
        self._last_power: str | None = None

    async def _async_update_data(self) -> ProjectorData:
        """Fetch state from the projector."""
        try:
            power = await self.client.query(KEY_POWER)
        except EscVp21Error as err:
            return self._handle_failure(err)

        if self.consecutive_failures:
            _LOGGER.debug(
                "Projector answered again after %d failed polls",
                self.consecutive_failures,
            )
        self.consecutive_failures = 0

        data = (
            replace(self.data, values=dict(self.data.values))
            if self.data
            else ProjectorData()
        )
        data.power = power
        now = dt_util.utcnow()
        if power != self._last_power:
            self._serial_rejected = False
            self._last_power = power

        # ERR? is only asked while on: the 6030UB rejects it in standby, and
        # the Error sensor keeps (and restores) its last value meanwhile.
        # LAMP? does answer in standby, so it is read at startup and
        # periodically there too.
        if power == PWR_ON:
            keys = list(ON_KEYS)  # includes ERR every cycle
            lamp_interval = LAMP_POLL_INTERVAL
        else:
            keys = []
            lamp_interval = LAMP_POLL_INTERVAL_OFF
        if self._due(KEY_LAMP, lamp_interval, now):
            keys.append(KEY_LAMP)
        if data.serial is None and not self._serial_rejected:
            keys.append(KEY_SERIAL)

        for key in keys:
            try:
                value = await self.client.query(key)
            except CommandRejected:
                if key == KEY_SERIAL:
                    self._serial_rejected = True
                    continue
                self._last_poll[key] = now
                if key in STICKY_KEYS:
                    # A rejection is not a fault: keep the last known value.
                    _LOGGER.debug("%s? rejected; keeping last value", key)
                    continue
                # On a shared bus a lone ERR may have been another client's.
                # Only clear a known value after it is rejected twice in a row.
                count = self._rejections.get(key, 0) + 1
                self._rejections[key] = count
                if count < 2 and self._has_value(data, key):
                    continue
                value = None
            except ResponseTimeout:
                _LOGGER.debug("No %s reply this cycle; keeping last value", key)
                continue
            except ConnectionFailed as err:
                _LOGGER.debug("Connection lost mid-poll (%s); keeping last values", err)
                break
            else:
                self._rejections.pop(key, None)
                self._last_poll[key] = now
            self._store(data, key, value)

        return data

    def _due(self, key: str, interval: timedelta, now: datetime) -> bool:
        last = self._last_poll.get(key)
        return last is None or now - last >= interval

    @staticmethod
    def _has_value(data: ProjectorData, key: str) -> bool:
        if key == KEY_LAMP:
            return data.lamp_hours is not None
        if key == KEY_SERIAL:
            return data.serial is not None
        return data.values.get(key) is not None

    def _store(self, data: ProjectorData, key: str, value: str | None) -> None:
        if key == KEY_LAMP:
            try:
                data.lamp_hours = int(value) if value is not None else None
            except ValueError:
                _LOGGER.debug("Unexpected LAMP value %r", value)
            return
        if key == KEY_SERIAL:
            if value:
                data.serial = value
                self._learned_serial(value)
            return
        data.values[key] = value
        if value is not None and key in SELECT_KEYS:
            codes = self.seen_codes.setdefault(key, set())
            if value not in codes:
                codes.add(value)
                self._codes_store.async_delay_save(self._codes_data, CODES_SAVE_DELAY)

    async def async_load_codes(self) -> None:
        """Load codes learned in earlier runs."""
        stored = await self._codes_store.async_load()
        if not stored:
            return
        for key, codes in stored.get("seen_codes", {}).items():
            if key in SELECT_KEYS:
                self.seen_codes.setdefault(key, set()).update(
                    str(code) for code in codes
                )

    async def async_save_codes(self) -> None:
        """Write learned codes now (on unload/reload)."""
        if self.seen_codes:
            await self._codes_store.async_save(self._codes_data())

    def _codes_data(self) -> dict[str, dict[str, list[str]]]:
        return {
            "seen_codes": {
                key: sorted(codes) for key, codes in self.seen_codes.items()
            }
        }

    def _handle_failure(self, err: EscVp21Error) -> ProjectorData:
        """Keep the last known state until failures pile up."""
        self.consecutive_failures += 1
        if self.data is None or self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        _LOGGER.debug(
            "Poll failed (%d/%d), keeping last state: %s",
            self.consecutive_failures,
            MAX_CONSECUTIVE_FAILURES,
            err,
        )
        return self.data

    def _learned_serial(self, serial: str) -> None:
        """Record the serial number on the device and, if needed, the entry."""
        entry = self.config_entry
        dev_reg = dr.async_get(self.hass)
        identifier = (DOMAIN, entry.entry_id)
        if hasattr(dev_reg, "async_get_device_by_identifier"):
            device = dev_reg.async_get_device_by_identifier(identifier, entry.entry_id)
        else:  # pragma: no cover - Home Assistant before 2026.x
            device = dev_reg.async_get_device(identifiers={identifier})
        if device:
            if device.serial_number != serial:
                dev_reg.async_update_device(device.id, serial_number=serial)
        if entry.unique_id == serial:
            return
        if any(
            other.unique_id == serial
            for other in self.hass.config_entries.async_entries(DOMAIN)
            if other.entry_id != entry.entry_id
        ):
            _LOGGER.warning(
                "Projector %s is configured more than once; remove one entry", serial
            )
            return
        self.hass.config_entries.async_update_entry(entry, unique_id=serial)

    async def async_set(self, key: str, value: str) -> None:
        """Send a set command and refresh afterwards."""
        if self.data and self.data.power in PWR_BUSY:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="projector_busy"
            )
        try:
            await self.client.set(key, value)
        except CommandRejected as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={"command": f"{key} {value}"},
            ) from err
        except EscVp21Error as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={
                    "command": f"{key} {value}",
                    "error": str(err),
                },
            ) from err
        finally:
            await self.async_request_refresh()
