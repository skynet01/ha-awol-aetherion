"""Binary sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AetherionConfigEntry, AetherionCoordinator
from .entity import AetherionEntity
from .protocol import ERROR_STATES

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AetherionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    async_add_entities([AetherionProblemSensor(entry.runtime_data)])


class AetherionProblemSensor(AetherionEntity, BinarySensorEntity):
    """On when the projector reports a hardware error (%3LEDS / %2ERST)."""

    _attr_translation_key = "problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AetherionCoordinator) -> None:
        """Init."""
        super().__init__(coordinator, "problem")

    @property
    def available(self) -> bool:
        """Available whenever the projector answers."""
        data = self.coordinator.data
        return (
            self.coordinator.last_update_success
            and data is not None
            and data.reachable
            and data.error is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Problem state."""
        return self.coordinator.data.error not in (None, 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Error description."""
        code = self.coordinator.data.error
        return {"error_code": code, "error": ERROR_STATES.get(code, "Unknown")}
