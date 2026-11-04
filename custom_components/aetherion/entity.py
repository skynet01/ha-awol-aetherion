"""Base entity for the AWOL Aetherion integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN, MANUFACTURER, device_key
from .coordinator import AetherionCoordinator


class AetherionEntity(CoordinatorEntity[AetherionCoordinator]):
    """Common entity bits."""

    _attr_has_entity_name = True
    # Picture/signal entities only make sense while the projector is on.
    _requires_power = True

    def __init__(self, coordinator: AetherionCoordinator, key: str) -> None:
        """Init."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        base_id = device_key(entry)
        self._attr_unique_id = f"{base_id}_{key}"
        dev = coordinator.device
        # No MAC connection on purpose: androidtv_remote registers the same MAC
        # and HA would merge both integrations into one device.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.title or dev.name or DEFAULT_NAME,
            manufacturer=dev.manufacturer or MANUFACTURER,
            model=dev.model,
            sw_version=dev.sw_version,
        )

    @property
    def available(self) -> bool:
        """Available if the last poll worked (and the projector is on, if needed)."""
        if not super().available or self.coordinator.data is None:
            return False
        if self._requires_power:
            return self.coordinator.data.controllable
        return True
