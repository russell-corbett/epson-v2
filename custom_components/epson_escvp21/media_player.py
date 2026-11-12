"""Media player entity: power and input only."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    KEY_POWER,
    KEY_SOURCE,
    PWR_ON,
    PWR_WARMING,
    SOURCES,
    option_to_code,
)
from .coordinator import EpsonConfigEntry
from .entity import EpsonEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EpsonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the media player."""
    async_add_entities([EpsonMediaPlayer(entry.runtime_data)])


class EpsonMediaPlayer(EpsonEntity, MediaPlayerEntity):
    """Projector power and source."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.PROJECTOR
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )
    _attr_source_list = list(SOURCES.values())

    def __init__(self, coordinator) -> None:
        """Initialize the media player."""
        super().__init__(coordinator, "media_player")

    @property
    def state(self) -> MediaPlayerState:
        """On while on or warming up; off for standby and cooling down.

        The power state sensor shows the exact PWR code.
        """
        if self.coordinator.data.power in (PWR_ON, PWR_WARMING):
            return MediaPlayerState.ON
        return MediaPlayerState.OFF

    @property
    def source(self) -> str | None:
        """Return the current input."""
        data = self.coordinator.data
        if not data.is_on or (code := data.get(KEY_SOURCE)) is None:
            return None
        return SOURCES.get(code, f"Code {code}")

    async def async_turn_on(self) -> None:
        """Turn the projector on."""
        await self.coordinator.async_set(KEY_POWER, "ON")

    async def async_turn_off(self) -> None:
        """Turn the projector off."""
        await self.coordinator.async_set(KEY_POWER, "OFF")

    async def async_select_source(self, source: str) -> None:
        """Select an input; sends SOURCE <code>."""
        code = option_to_code(source, SOURCES)
        if code is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_source",
                translation_placeholders={"source": source},
            )
        await self.coordinator.async_set(KEY_SOURCE, code)
