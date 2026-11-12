"""Button: switch to HDMI1."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import KEY_SOURCE
from .coordinator import EpsonConfigEntry, EpsonCoordinator
from .entity import EpsonOnlyWhenOnEntity

PARALLEL_UPDATES = 0

HDMI1_CODE = "30"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EpsonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button."""
    async_add_entities([EpsonHdmi1Button(entry.runtime_data)])


class EpsonHdmi1Button(EpsonOnlyWhenOnEntity, ButtonEntity):
    """Sends SOURCE 30."""

    _attr_translation_key = "switch_to_hdmi1"

    def __init__(self, coordinator: EpsonCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "switch_to_hdmi1")

    async def async_press(self) -> None:
        """Switch to HDMI1."""
        await self.coordinator.async_set(KEY_SOURCE, HDMI1_CODE)
