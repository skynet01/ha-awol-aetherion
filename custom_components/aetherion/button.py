"""Button entities."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Set up buttons."""
    async_add_entities([AetherionFocusButton(entry.runtime_data)])


class AetherionFocusButton(AetherionEntity, ButtonEntity):
    """Open the manual focus screen."""

    _attr_translation_key = "manual_focus"

    def __init__(self, coordinator: AetherionCoordinator) -> None:
        """Init."""
        super().__init__(coordinator, "manual_focus")

    async def async_press(self) -> None:
        """Press."""
        await self.coordinator.async_run(self.coordinator.client.manual_focus())
