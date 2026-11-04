"""Select entities: picture mode and picture settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import AetherionConfigEntry, AetherionCoordinator, AetherionData
from .entity import AetherionEntity
from .protocol import (
    COLOR_TEMPERATURES,
    GAMMA_MODES,
    GAMMA_SETTABLE,
    MOTION_MODES,
    TONE_MAPPING_MODES,
)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class CodeSelectDescription(SelectEntityDescription):
    """A select backed by a numeric class-3 command."""

    command: str
    table: dict[int, str]
    value_fn: Callable[[AetherionData], int | None]
    field: str
    settable: tuple[int, ...] | None = None


CODE_SELECTS: tuple[CodeSelectDescription, ...] = (
    CodeSelectDescription(
        key="motion_enhancement",
        translation_key="motion_enhancement",
        command="MOTN",
        table=MOTION_MODES,
        value_fn=lambda d: d.motion,
        field="motion",
        entity_category=EntityCategory.CONFIG,
    ),
    CodeSelectDescription(
        key="gamma",
        translation_key="gamma",
        command="GAMA",
        table=GAMMA_MODES,
        settable=GAMMA_SETTABLE,
        value_fn=lambda d: d.gamma,
        field="gamma",
        entity_category=EntityCategory.CONFIG,
    ),
    CodeSelectDescription(
        key="dynamic_tone_mapping",
        translation_key="dynamic_tone_mapping",
        command="DYTM",
        table=TONE_MAPPING_MODES,
        value_fn=lambda d: d.tone_mapping,
        field="tone_mapping",
        entity_category=EntityCategory.CONFIG,
    ),
    CodeSelectDescription(
        key="color_temperature",
        translation_key="color_temperature",
        command="CLTP",
        table=COLOR_TEMPERATURES,
        value_fn=lambda d: d.color_temperature,
        field="color_temperature",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AetherionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up selects."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            AetherionPictureModeSelect(coordinator),
            *(AetherionCodeSelect(coordinator, desc) for desc in CODE_SELECTS),
        ]
    )


class AetherionPictureModeSelect(AetherionEntity, SelectEntity):
    """Picture mode; options come from %3PICL and depend on the signal type."""

    _attr_translation_key = "picture_mode"

    def __init__(self, coordinator: AetherionCoordinator) -> None:
        """Init."""
        super().__init__(coordinator, "picture_mode")

    @property
    def available(self) -> bool:
        """Needs a mode list."""
        return super().available and bool(self.options)

    @property
    def options(self) -> list[str]:
        """Modes for the current signal."""
        data = self.coordinator.data
        modes = list(data.picture_modes)
        if data.picture_mode and data.picture_mode not in modes:
            modes.append(data.picture_mode)
        return modes

    @property
    def current_option(self) -> str | None:
        """Current mode."""
        return self.coordinator.data.picture_mode

    async def async_select_option(self, option: str) -> None:
        """Set mode."""
        await self.coordinator.async_run(
            self.coordinator.client.set(3, "PICT", option), picture_mode=option
        )


class AetherionCodeSelect(AetherionEntity, SelectEntity):
    """Select mapped onto numeric codes."""

    entity_description: CodeSelectDescription

    def __init__(
        self, coordinator: AetherionCoordinator, description: CodeSelectDescription
    ) -> None:
        """Init."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Unavailable when the setting does not apply (e.g. tone mapping in SDR)."""
        return (
            super().available
            and self.entity_description.value_fn(self.coordinator.data) is not None
        )

    @property
    def options(self) -> list[str]:
        """Settable options, plus the current read-only value if any."""
        desc = self.entity_description
        codes = list(desc.settable or desc.table)
        current = desc.value_fn(self.coordinator.data)
        if current in desc.table and current not in codes:
            codes.append(current)
        return [desc.table[c] for c in codes]

    @property
    def current_option(self) -> str | None:
        """Current option."""
        code = self.entity_description.value_fn(self.coordinator.data)
        return self.entity_description.table.get(code) if code is not None else None

    async def async_select_option(self, option: str) -> None:
        """Set option."""
        desc = self.entity_description
        code = next((c for c, name in desc.table.items() if name == option), None)
        if code is None or (desc.settable is not None and code not in desc.settable):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="option_read_only",
                translation_placeholders={"option": option},
            )
        await self.coordinator.async_run(
            self.coordinator.client.set(3, desc.command, code), **{desc.field: code}
        )
