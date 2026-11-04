"""Sensor entities: signal info, picture size, temperatures, fans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AetherionConfigEntry, AetherionCoordinator, AetherionData
from .entity import AetherionEntity
from .protocol import ASPECT_RATIOS, FAN_COUNT, TEMPERATURE_SENSOR_COUNT

PARALLEL_UPDATES = 0

# Sensors reported as non-zero on the sample unit; the rest start disabled.
# Shown by default; the rest start hidden (still enabled, one click to show).
VISIBLE_TEMPERATURES = 2
NO_SIGNAL = "No signal"


@dataclass(frozen=True, kw_only=True)
class AetherionSensorDescription(SensorEntityDescription):
    """Sensor description with a value getter."""

    value_fn: Callable[[AetherionData], str | int | None]
    # Slot reported as 0 while on = not fitted on this model -> hide it.
    hide_when_zero: bool = False


def _signal(attr: str) -> Callable[[AetherionData], str | None]:
    return lambda d: getattr(d.signal, attr) if d.signal else None


def _index(attr: str, i: int) -> Callable[[AetherionData], int | None]:
    def get(d: AetherionData) -> int | None:
        values = getattr(d, attr)
        return values[i] if len(values) > i else None

    return get


SENSORS: tuple[AetherionSensorDescription, ...] = (
    # Read-only: the projector refuses %3LASL changes while dynamic iris /
    # adaptive laser control is on, which is the common case.
    AetherionSensorDescription(
        key="laser_level",
        translation_key="laser_level",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.laser,
    ),
    AetherionSensorDescription(
        key="input_resolution",
        translation_key="input_resolution",
        value_fn=lambda d: d.input_resolution or (NO_SIGNAL if d.no_signal else None),
    ),
    AetherionSensorDescription(
        key="recommended_resolution",
        translation_key="recommended_resolution",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        value_fn=lambda d: d.recommended_resolution,
    ),
    AetherionSensorDescription(
        key="picture_size",
        translation_key="picture_size",
        value_fn=lambda d: ASPECT_RATIOS.get(d.aspect) if d.aspect is not None else None,
    ),
    AetherionSensorDescription(
        key="signal_timing",
        translation_key="signal_timing",
        value_fn=lambda d: (d.signal.timing if d.signal else None)
        or (NO_SIGNAL if d.no_signal else None),
    ),
    AetherionSensorDescription(
        key="hdr_format",
        translation_key="hdr_format",
        value_fn=_signal("hdr_format"),
    ),
    AetherionSensorDescription(
        key="audio_format",
        translation_key="audio_format",
        value_fn=_signal("audio_format"),
    ),
    AetherionSensorDescription(
        key="color_depth",
        translation_key="color_depth",
        entity_registry_visible_default=False,
        value_fn=_signal("color_depth"),
    ),
    AetherionSensorDescription(
        key="color_format",
        translation_key="color_format",
        entity_registry_visible_default=False,
        value_fn=_signal("color_format"),
    ),
    AetherionSensorDescription(
        key="color_primaries",
        translation_key="color_primaries",
        entity_registry_visible_default=False,
        value_fn=_signal("color_primaries"),
    ),
    *(
        AetherionSensorDescription(
            key=f"temperature_{i + 1}",
            translation_key="temperature",
            translation_placeholders={"index": str(i + 1)},
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_visible_default=i < VISIBLE_TEMPERATURES,
            hide_when_zero=True,
            value_fn=_index("temperatures", i),
        )
        for i in range(TEMPERATURE_SENSOR_COUNT)
    ),
    *(
        AetherionSensorDescription(
            key=f"fan_{i + 1}",
            translation_key="fan_speed",
            translation_placeholders={"index": str(i + 1)},
            native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_visible_default=False,
            hide_when_zero=True,
            value_fn=_index("fans", i),
        )
        for i in range(FAN_COUNT)
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AetherionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    async_add_entities(AetherionSensor(coordinator, desc) for desc in SENSORS)


class AetherionSensor(AetherionEntity, SensorEntity):
    """Generic sensor."""

    entity_description: AetherionSensorDescription

    def __init__(
        self, coordinator: AetherionCoordinator, description: AetherionSensorDescription
    ) -> None:
        """Init."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Apply zero-slot hiding right away, not only on the next poll."""
        await super().async_added_to_hass()
        self._async_check_zero()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._async_check_zero()
        super()._handle_coordinator_update()

    @callback
    def _async_check_zero(self) -> None:
        """Hide temperature/fan slots this model doesn't have (they read 0)."""
        desc = self.entity_description
        data = self.coordinator.data
        if desc.hide_when_zero and data is not None and data.controllable:
            value = desc.value_fn(data)
            if value is not None:
                self._async_update_hidden(value == 0)

    @callback
    def _async_update_hidden(self, hide: bool) -> None:
        entry = self.registry_entry
        if entry is None:
            return
        registry = er.async_get(self.hass)
        if hide and entry.hidden_by is None:
            registry.async_update_entity(
                self.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
            )
        elif (
            not hide
            and entry.hidden_by is er.RegistryEntryHider.INTEGRATION
            and self.entity_description.entity_registry_visible_default
        ):
            # Only undo our own zero-hiding, never the hidden-by-default ones.
            registry.async_update_entity(self.entity_id, hidden_by=None)

    @property
    def native_value(self) -> str | int | None:
        """Value."""
        return self.entity_description.value_fn(self.coordinator.data)
