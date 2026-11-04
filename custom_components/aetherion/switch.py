"""Switch entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AetherionConfigEntry, AetherionCoordinator
from .entity import AetherionEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AetherionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switches."""
    async_add_entities([AetherionEblSwitch(entry.runtime_data)])


class AetherionEblSwitch(AetherionEntity, SwitchEntity):
    """Enhanced black level (%3ENBL 0/4)."""

    _attr_translation_key = "enhanced_black_level"

    def __init__(self, coordinator: AetherionCoordinator) -> None:
        """Init."""
        super().__init__(coordinator, "enhanced_black_level")

    @property
    def available(self) -> bool:
        """Unavailable when the projector does not report a value."""
        return super().available and self.coordinator.data.ebl is not None

    @property
    def is_on(self) -> bool | None:
        """State."""
        return self.coordinator.data.ebl

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable."""
        await self.coordinator.async_run(
            self.coordinator.client.set(3, "ENBL", 4), ebl=True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable."""
        await self.coordinator.async_run(
            self.coordinator.client.set(3, "ENBL", 0), ebl=False
        )
