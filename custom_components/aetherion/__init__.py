"""The AWOL Aetherion projector integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant, callback

from .const import DATA_LISTENER, DOMAIN
from .coordinator import AetherionConfigEntry, AetherionCoordinator
from .protocol import DEFAULT_PORT, AetherionClient, NotificationListener

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_get_listener(hass: HomeAssistant) -> NotificationListener | None:
    """Return the shared UDP listener, starting it on first use.

    Returns None if UDP port 4352 is already taken; the integration then relies
    on polling only.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    lock: asyncio.Lock = domain_data.setdefault("listener_lock", asyncio.Lock())
    async with lock:
        return await _async_start_listener(hass, domain_data)


async def _async_start_listener(
    hass: HomeAssistant, domain_data: dict
) -> NotificationListener | None:
    if DATA_LISTENER in domain_data:
        return domain_data[DATA_LISTENER]
    listener = NotificationListener()
    try:
        await listener.start()
    except OSError as err:
        _LOGGER.warning(
            "Cannot listen on UDP %s (%s); status notifications and discovery "
            "are disabled, polling still works",
            listener.port,
            err,
        )
        domain_data[DATA_LISTENER] = None
        return None

    @callback
    def _stop(_: Event) -> None:
        listener.stop()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)
    domain_data[DATA_LISTENER] = listener
    return listener


async def async_setup_entry(hass: HomeAssistant, entry: AetherionConfigEntry) -> bool:
    """Set up from a config entry."""
    client = AetherionClient(
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data.get(CONF_PASSWORD),
    )
    coordinator = AetherionCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    entry.async_on_unload(client.close)
    if (listener := await async_get_listener(hass)) is not None:
        entry.async_on_unload(
            listener.subscribe(
                entry.data[CONF_HOST], coordinator.async_handle_notification
            )
        )
        # Unicast search registers HA as the notification target.
        listener.send_search(entry.data[CONF_HOST])

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: AetherionConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AetherionConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
