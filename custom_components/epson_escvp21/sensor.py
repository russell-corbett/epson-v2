"""Sensors: lamp hours, power state, error."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import KEY_ERROR, POWER_STATE_UNKNOWN, POWER_STATES
from .coordinator import EpsonConfigEntry, EpsonCoordinator, ProjectorData
from .entity import EpsonEntity

PARALLEL_UPDATES = 0


def _power_state(data: ProjectorData) -> str | None:
    if data.power is None:
        return None
    return POWER_STATES.get(data.power, POWER_STATE_UNKNOWN)


def _error(data: ProjectorData) -> str | None:
    code = data.get(KEY_ERROR)
    if code is None:
        return None
    return "OK" if code == "00" else f"Code {code}"


@dataclass(frozen=True, kw_only=True)
class EpsonSensorDescription(SensorEntityDescription):
    """Describes a projector sensor."""

    value_fn: Callable[[ProjectorData], str | int | None]
    # Show the value from before a restart until the projector answers.
    restore: bool = False


SENSORS: tuple[EpsonSensorDescription, ...] = (
    EpsonSensorDescription(
        key="lamp_hours",
        translation_key="lamp_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.lamp_hours,
        restore=True,
    ),
    EpsonSensorDescription(
        key="power_state",
        translation_key="power_state",
        device_class=SensorDeviceClass.ENUM,
        options=[*POWER_STATES.values(), POWER_STATE_UNKNOWN],
        value_fn=_power_state,
    ),
    EpsonSensorDescription(
        key="error",
        translation_key="error",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_error,
        restore=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EpsonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_entities(
        EpsonSensor(entry.runtime_data, description) for description in SENSORS
    )


class EpsonSensor(EpsonEntity, RestoreSensor):
    """A projector sensor."""

    entity_description: EpsonSensorDescription

    def __init__(
        self, coordinator: EpsonCoordinator, description: EpsonSensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._restored: str | int | float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last value for sensors that keep one."""
        await super().async_added_to_hass()
        if not self.entity_description.restore:
            return
        if (last := await self.async_get_last_sensor_data()) is not None:
            value = last.native_value
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            self._restored = value

    @property
    def native_value(self) -> str | int | float | None:
        """Return the live value, else the one from before the restart."""
        value = self.entity_description.value_fn(self.coordinator.data)
        if value is None and self.entity_description.restore:
            return self._restored
        return value
