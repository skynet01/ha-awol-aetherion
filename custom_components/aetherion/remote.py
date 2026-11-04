"""Remote entity: navigation keys and raw protocol commands."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import (
    ATTR_DELAY_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    RemoteEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import AetherionConfigEntry, AetherionCoordinator
from .entity import AetherionEntity
from .protocol import REMOTE_KEY_ALIASES, REMOTE_KEYS

PARALLEL_UPDATES = 1

# Extra pseudo-keys handled by other protocol commands.
EXTRA_KEYS = ("volume_up", "volume_down", "mute", "unmute", "focus", "power_on", "power_off")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AetherionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the remote."""
    async_add_entities([AetherionRemote(entry.runtime_data)])


class AetherionRemote(AetherionEntity, RemoteEntity):
    """Send key presses to the projector.

    ``command`` accepts key names (up, down, left, right, ok, back, menu,
    settings, home, volume_up, volume_down, mute, unmute, focus, power_on,
    power_off) or raw frames such as ``%3LASL 8``.
    """

    _attr_name = None
    _attr_translation_key = "remote"
    _requires_power = False
    _unrecorded_attributes = frozenset({"keys"})

    def __init__(self, coordinator: AetherionCoordinator) -> None:
        """Init."""
        super().__init__(coordinator, "remote")

    @property
    def is_on(self) -> bool:
        """Power state."""
        return self.coordinator.data.is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List available keys."""
        return {"keys": [*REMOTE_KEYS, *EXTRA_KEYS]}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Power on."""
        await self.coordinator.async_turn_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Standby."""
        await self.coordinator.async_turn_off()

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send keys."""
        repeats = kwargs.get(ATTR_NUM_REPEATS, 1)
        delay = kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS)
        commands = list(command)
        for cmd in commands:
            self._validate(cmd)
        first = True
        for _ in range(repeats):
            for cmd in commands:
                if not first and delay:
                    await asyncio.sleep(delay)
                first = False
                key = cmd.strip().lower()
                # Same paths as the toggle, so wake and power-cut logic applies.
                if key == "power_on":
                    await self.coordinator.async_turn_on()
                elif key == "power_off":
                    await self.coordinator.async_turn_off()
                else:
                    await self.coordinator.async_run(self._client_call(cmd))

    @staticmethod
    def _validate(cmd: str) -> None:
        key = cmd.strip().lower()
        key = REMOTE_KEY_ALIASES.get(key, key)
        if cmd.strip().startswith("%") or key in REMOTE_KEYS or key in EXTRA_KEYS:
            return
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_remote_command",
            translation_placeholders={
                "command": cmd,
                "options": ", ".join([*REMOTE_KEYS, *EXTRA_KEYS]),
            },
        )

    def _client_call(self, cmd: str):
        client = self.coordinator.client
        key = cmd.strip().lower()
        if cmd.strip().startswith("%"):
            return client.send_raw(cmd)
        match key:
            case "volume_up":
                return client.volume_step(True)
            case "volume_down":
                return client.volume_step(False)
            case "mute":
                return client.set_mute(True)
            case "unmute":
                return client.set_mute(False)
            case "focus":
                return client.manual_focus()
        return client.remote_key(key)
