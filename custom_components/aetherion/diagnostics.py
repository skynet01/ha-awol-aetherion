"""Diagnostics support."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_MAC, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import AetherionConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_MAC}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AetherionConfigEntry
) -> dict[str, Any]:
    """Return diagnostics."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "device": asdict(coordinator.device),
        "data": asdict(coordinator.data) if coordinator.data else None,
        "last_update_success": coordinator.last_update_success,
    }
