"""Base entity for the Epson ESC/VP21 integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import EpsonCoordinator


class EpsonEntity(CoordinatorEntity[EpsonCoordinator]):
    """Common entity: one device per config entry."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EpsonCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            name=entry.title,
            serial_number=coordinator.data.serial if coordinator.data else None,
        )

    @property
    def available(self) -> bool:
        """Available while polls succeed and the power state is known."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.power is not None
        )


class EpsonOnlyWhenOnEntity(EpsonEntity):
    """Entity that is only meaningful while the projector is on (PWR=01)."""

    @property
    def available(self) -> bool:
        """Available only while the projector is on."""
        return super().available and self.coordinator.data.is_on
