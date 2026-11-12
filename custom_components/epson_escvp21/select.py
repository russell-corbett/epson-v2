"""Select entities: input, color mode, aspect, lamp mode."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ASPECTS,
    CMODES,
    DOMAIN,
    KEY_ASPECT,
    KEY_CMODE,
    KEY_LUMINANCE,
    KEY_SOURCE,
    LUMINANCE_MODES,
    SOURCES,
    code_to_option,
    option_to_code,
)
from .coordinator import EpsonConfigEntry, EpsonCoordinator
from .entity import EpsonOnlyWhenOnEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EpsonSelectDescription(SelectEntityDescription):
    """Describes an ESC/VP21 setting exposed as a select."""

    names: dict[str, str]


SELECTS: tuple[EpsonSelectDescription, ...] = (
    EpsonSelectDescription(
        key=KEY_SOURCE, translation_key="source", names=SOURCES
    ),
    EpsonSelectDescription(
        key=KEY_CMODE,
        translation_key="color_mode",
        names=CMODES,
        entity_category=EntityCategory.CONFIG,
    ),
    EpsonSelectDescription(
        key=KEY_ASPECT,
        translation_key="aspect",
        names=ASPECTS,
        entity_category=EntityCategory.CONFIG,
    ),
    EpsonSelectDescription(
        key=KEY_LUMINANCE,
        translation_key="lamp_mode",
        names=LUMINANCE_MODES,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EpsonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects."""
    async_add_entities(
        EpsonSelect(entry.runtime_data, description) for description in SELECTS
    )


class EpsonSelect(EpsonOnlyWhenOnEntity, SelectEntity):
    """A projector setting with a fixed set of codes."""

    entity_description: EpsonSelectDescription

    def __init__(
        self, coordinator: EpsonCoordinator, description: EpsonSelectDescription
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator, description.key.lower())
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Available while on and the value is known."""
        return (
            super().available
            and self.coordinator.data.get(self.entity_description.key) is not None
        )

    @property
    def options(self) -> list[str]:
        """Known names plus any unnamed codes seen on this projector."""
        names = self.entity_description.names
        seen = self.coordinator.seen_codes.get(self.entity_description.key, set())
        extra = sorted(code for code in seen if code not in names)
        return list(names.values()) + [code_to_option(c, names) for c in extra]

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        code = self.coordinator.data.get(self.entity_description.key)
        return None if code is None else code_to_option(code, self.entity_description.names)

    async def async_select_option(self, option: str) -> None:
        """Send the code for the chosen option."""
        code = option_to_code(option, self.entity_description.names)
        if code is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_option",
                translation_placeholders={"option": option},
            )
        await self.coordinator.async_set(self.entity_description.key, code)
