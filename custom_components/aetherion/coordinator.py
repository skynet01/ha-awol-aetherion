"""Polling coordinator for the AWOL Aetherion projector."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_POWER_OFF_DELAY,
    CONF_POWER_OFF_ENTITY,
    CONF_WAKE_ENTITY,
    CONF_WAKE_HOST,
    CONF_WOL_MAC,
    DEFAULT_POWER_OFF_DELAY,
    DOMAIN,
    MANUFACTURER,
    OFF_CONNECT_TIMEOUT,
    OFF_SCAN_INTERVAL,
    POWER_OFF_CHECK_SECONDS,
    POWER_OFF_CONFIRM_TIMEOUT,
    SCAN_INTERVAL,
    WAKE_DOMAINS,
    WAKE_GRACE_SECONDS,
    WAKE_TIMEOUT_SECONDS,
    WAKING_SCAN_INTERVAL,
    device_key,
    wol_enabled,
)
from .protocol import (
    POWER_ON,
    POWER_STANDBY,
    POWER_WARMING,
    AetherionAuthError,
    AetherionClient,
    AetherionCommandError,
    AetherionConnectionError,
    AetherionError,
    SignalInfo,
    normalize_resolution,
    parse_int_list,
    send_magic_packet,
)

_LOGGER = logging.getLogger(__name__)

# Slow-changing values are refreshed every Nth poll.
SLOW_POLL_EVERY = 6
# Consecutive connection failures before we report the projector as off.
OFFLINE_AFTER_FAILURES = 2

COMMAND_ERROR_KEYS = {
    "ERR1": "command_unsupported",
    "ERR2": "command_out_of_range",
    "ERR3": "command_unavailable",
    "ERR4": "projector_failure",
}

type AetherionConfigEntry = ConfigEntry[AetherionCoordinator]


@dataclass(slots=True)
class DeviceInfo:
    """Static identity of the projector."""

    name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    color: str | None = None
    sw_version: str | None = None


@dataclass(slots=True)
class AetherionData:
    """Snapshot of projector state."""

    power: int | None = None
    input: int | None = None
    inputs: list[int] = field(default_factory=list)
    muted: bool | None = None
    volume: int | None = None
    picture_mode: str | None = None
    picture_modes: list[str] = field(default_factory=list)
    laser: int | None = None
    ebl: bool | None = None
    motion: int | None = None
    gamma: int | None = None
    tone_mapping: int | None = None
    color_temperature: int | None = None
    aspect: int | None = None
    signal: SignalInfo | None = None
    input_resolution: str | None = None
    recommended_resolution: str | None = None
    temperatures: list[int] = field(default_factory=list)
    fans: list[int] = field(default_factory=list)
    error: int | None = None
    # False when the control port is closed (the projector shuts it in standby).
    reachable: bool = True
    # True right after a wake request, before the control port has opened.
    waking: bool = False

    @property
    def is_on(self) -> bool:
        """Return True if powered on (or warming up)."""
        return (self.reachable or self.waking) and self.power in (
            POWER_ON,
            POWER_WARMING,
        )

    @property
    def no_signal(self) -> bool:
        """True when on but the projector reports no input signal (e.g. Home)."""
        return self.controllable and self.signal is not None and self.signal.timing is None

    @property
    def controllable(self) -> bool:
        """Return True if on and the control port answers."""
        return self.reachable and self.is_on


def _int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _text(raw: str | None) -> str | None:
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


class AetherionCoordinator(DataUpdateCoordinator[AetherionData]):
    """Fetch state from the projector."""

    config_entry: AetherionConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: AetherionConfigEntry, client: AetherionClient
    ) -> None:
        """Init."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=1.0, immediate=False
            ),
        )
        self.client = client
        self.device = DeviceInfo()
        self._poll_count = 0
        self._failures = 0
        self._wake_until = 0.0
        # Bumped by every pushed update (command, notification) so a slower
        # poll that started earlier can't overwrite newer state.
        self._data_gen = 0
        # Bumped by power-off so an in-flight wake can't power back on.
        self._power_gen = 0
        self._power_cut_task: asyncio.Task | None = None

    async def async_fetch_device_info(self) -> None:
        """Read identity values (called at setup and when firmware is unknown)."""
        c = self.client
        self.device.name = _text(await c.try_query(1, "NAME")) or self.device.name
        self.device.manufacturer = (
            _text(await c.try_query(1, "INF1")) or self.device.manufacturer
        )
        self.device.model = _text(await c.try_query(1, "INF2")) or self.device.model
        self.device.color = _text(await c.try_query(1, "INFO")) or self.device.color
        self.device.sw_version = (
            _text(await c.try_query(2, "SVER")) or self.device.sw_version
        )

    async def _async_update_data(self) -> AetherionData:
        generation = self._data_gen
        data = await self._async_poll()
        if generation != self._data_gen and self.data is not None:
            # State was pushed while we polled; our snapshot may predate it.
            self.hass.async_create_task(self.async_request_refresh())
            return self.data
        self._async_adapt_polling(data)
        return data

    @callback
    def _async_adapt_polling(self, data: AetherionData) -> None:
        """Poll slowly while off, quickly while booting after a wake request."""
        if data.waking:
            interval = WAKING_SCAN_INTERVAL
        elif data.controllable:
            interval = SCAN_INTERVAL
        else:
            interval = OFF_SCAN_INTERVAL
        self.update_interval = interval
        self.client.connect_timeout = (
            self.client.timeout if data.controllable else OFF_CONNECT_TIMEOUT
        )

    @callback
    def async_set_updated_data(self, data: AetherionData) -> None:
        """Push data (optimistic or notification) and adapt the poll rate."""
        self._data_gen += 1
        self._async_adapt_polling(data)
        super().async_set_updated_data(data)

    async def _async_poll(self) -> AetherionData:
        try:
            data = await self._fetch()
        except AetherionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except AetherionConnectionError as err:
            # In standby the projector closes its control port (and may drop
            # Ethernet entirely), so "unreachable" normally means "off".
            self._failures += 1
            if self._failures < OFFLINE_AFTER_FAILURES and self.data is not None:
                _LOGGER.debug("Poll failed, keeping last state: %s", err)
                return self.data
            if self.hass.loop.time() < self._wake_until:
                if self.data is not None and self.data.waking:
                    return self.data
                return AetherionData(
                    power=POWER_WARMING,
                    reachable=False,
                    waking=True,
                    inputs=self.data.inputs if self.data else [],
                )
            if self.data is not None and self.data.waking:
                _LOGGER.warning(
                    "Projector did not wake within %s s; check that network "
                    "standby is enabled on the projector",
                    WAKE_GRACE_SECONDS,
                )
            _LOGGER.debug("Projector unreachable, reporting off: %s", err)
            return AetherionData(
                power=0,
                reachable=False,
                inputs=self.data.inputs if self.data else [],
            )
        except AetherionError as err:
            raise UpdateFailed(str(err)) from err
        self._failures = 0
        self._wake_until = 0.0
        return data

    async def _fetch(self) -> AetherionData:
        c = self.client
        prev = self.data or AetherionData()
        slow = self._poll_count % SLOW_POLL_EVERY == 0
        self._poll_count += 1

        power = _int(await c.query(1, "POWR"))
        if power == POWER_STANDBY and self.hass.loop.time() < self._wake_until:
            # Woken over the network but still in standby: finish the job.
            _LOGGER.debug("Network is up after wake, sending power on")
            await c.power_on()
            power = POWER_WARMING
        if self.device.model is None:
            await self.async_fetch_device_info()
            self._async_update_device_registry()
        data = AetherionData(power=power, error=_int(await c.try_query(3, "LEDS")))
        data.inputs = prev.inputs
        if not data.is_on:
            return data

        data.input = _int(await c.try_query(1, "INPT"))
        if slow or not prev.inputs:
            data.inputs = parse_int_list(await c.try_query(1, "INST")) or prev.inputs
        avmt = _int(await c.try_query(1, "AVMT"))
        data.muted = None if avmt is None else avmt in (21, 31)  # 11 = video mute
        data.volume = _int(await c.try_query(3, "VOLM"))

        sinf = await c.try_query(3, "SINF")
        data.signal = SignalInfo.parse(sinf) if sinf else None
        data.picture_mode = _text(await c.try_query(3, "PICT"))
        hdr_changed = (data.signal and data.signal.hdr_format) != (
            prev.signal and prev.signal.hdr_format
        )
        if (
            slow
            or hdr_changed
            or not prev.picture_modes
            or data.picture_mode not in prev.picture_modes
        ):
            raw = await c.try_query(3, "PICL")
            data.picture_modes = (
                [m.strip() for m in raw.split(",") if m.strip()] if raw else []
            )
        else:
            data.picture_modes = prev.picture_modes

        data.laser = _int(await c.try_query(3, "LASL"))
        ebl = _int(await c.try_query(3, "ENBL"))
        data.ebl = None if ebl is None else ebl != 0
        data.motion = _int(await c.try_query(3, "MOTN"))
        data.gamma = _int(await c.try_query(3, "GAMA"))
        data.tone_mapping = _int(await c.try_query(3, "DYTM"))
        data.color_temperature = _int(await c.try_query(3, "CLTP"))
        data.aspect = _int(await c.try_query(3, "ASPR"))
        data.input_resolution = normalize_resolution(await c.try_query(2, "IRES"))

        if slow or not prev.temperatures:
            data.recommended_resolution = normalize_resolution(
                await c.try_query(2, "RRES")
            )
            data.temperatures = parse_int_list(await c.try_query(3, "TEMP"))
            data.fans = parse_int_list(await c.try_query(3, "FANS"))
            if self.device.sw_version is None:
                self.device.sw_version = _text(await c.try_query(2, "SVER"))
                if self.device.sw_version:
                    self._async_update_device_registry()
        else:
            data.recommended_resolution = prev.recommended_resolution
            data.temperatures = prev.temperatures
            data.fans = prev.fans
        return data

    @callback
    def _async_update_device_registry(self) -> None:
        """Push model/firmware learned after setup to our device entry."""
        registry = dr.async_get(self.hass)
        identifier = (DOMAIN, device_key(self.config_entry))
        device = registry.async_get_device_by_identifier(
            identifier, self.config_entry.entry_id
        )
        if device is None:
            return
        changes = {
            "manufacturer": self.device.manufacturer or MANUFACTURER,
            "model": self.device.model,
            "sw_version": self.device.sw_version,
        }
        changes = {k: v for k, v in changes.items() if v and getattr(device, k) != v}
        if changes:
            registry.async_update_device(device.id, **changes)

    async def async_turn_on(self) -> None:
        """Power on; if the control port is closed, wake the projector first.

        Waking uses Wake-on-LAN (works once the projector is in "network
        optimized" standby) and/or a configured wake entity. The call returns
        right away; the entity shows "on" while booting and polls quickly.
        """
        self._async_cancel_power_cut()
        if self.data is None or self.data.reachable:
            try:
                await self.async_run(self.client.power_on(), power=POWER_WARMING)
                return
            except HomeAssistantError as err:
                if not isinstance(err.__cause__, AetherionConnectionError):
                    raise
                woken = await self._async_wake()
                if not woken:
                    raise HomeAssistantError(
                        translation_domain=DOMAIN, translation_key="standby_port_closed"
                    ) from err
                return
        if not await self._async_wake():
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="standby_port_closed"
            )

    async def async_turn_off(self) -> None:
        """Standby, then cut the linked power entity once it is safe (if set up).

        Also cancels a pending wake so the projector isn't powered back on. Power
        is only ever cut after the projector accepted this off command: if it
        can't be reached we can't tell "already off" from "on, network down".
        """
        self._wake_until = 0.0
        self._power_gen += 1
        self._async_cancel_power_cut()
        data = self.data
        if data is not None and not data.reachable and not data.waking:
            _LOGGER.debug("Already off (control port closed), nothing to send")
            return
        await self.async_run(self.client.power_off(), power=POWER_STANDBY)
        options = self.config_entry.options
        entity_id = options.get(CONF_WAKE_ENTITY)
        if entity_id and options.get(CONF_POWER_OFF_ENTITY):
            self._power_cut_task = self.config_entry.async_create_background_task(
                self.hass,
                self._async_power_cut(entity_id),
                "aetherion_power_cut",
            )

    @callback
    def _async_cancel_power_cut(self) -> None:
        if self._power_cut_task is not None and not self._power_cut_task.done():
            self._power_cut_task.cancel()
        self._power_cut_task = None

    async def _async_probe_power(self) -> int | None:
        """Ask for the power state. None = port closed, -1 = no usable answer."""
        try:
            value = _int(await self.client.query(1, "POWR"))
        except AetherionConnectionError:
            return None
        except AetherionError as err:
            _LOGGER.debug("Power query failed: %s", err)
            return -1
        return -1 if value is None else value

    async def _async_wait_until_off(self) -> bool:
        """Return True once the projector is confirmed off.

        Off means its control port closed (it does that a few seconds into
        standby) on two checks in a row, or that it still reports standby when
        time runs out. Anything else counts as not confirmed.
        """
        deadline = time.monotonic() + POWER_OFF_CONFIRM_TIMEOUT
        closed = 0
        power: int | None = None
        while time.monotonic() < deadline:
            power = await self._async_probe_power()
            closed = closed + 1 if power is None else 0
            if closed >= 2:
                return True
            await asyncio.sleep(POWER_OFF_CHECK_SECONDS)
        return power == POWER_STANDBY

    async def _async_power_cut(self, entity_id: str) -> None:
        """Turn the power entity off, but only once the projector is fully off."""
        if not await self._async_wait_until_off():
            _LOGGER.warning(
                "Projector did not confirm it is off within %s s; leaving %s on",
                POWER_OFF_CONFIRM_TIMEOUT,
                entity_id,
            )
            return
        options = self.config_entry.options
        deadline = time.monotonic() + options.get(
            CONF_POWER_OFF_DELAY, DEFAULT_POWER_OFF_DELAY
        )
        while True:
            # Keep checking during the cool-down: it may have been turned back
            # on with the remote. Checked once more right before cutting power.
            power = await self._async_probe_power()
            if power not in (None, POWER_STANDBY):
                _LOGGER.info(
                    "Projector is no longer off (power=%s); leaving %s on",
                    power,
                    entity_id,
                )
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, POWER_OFF_CHECK_SECONDS))
        domain = entity_id.split(".", 1)[0]
        _LOGGER.debug("Projector is fully off, turning off %s", entity_id)
        try:
            await self.hass.services.async_call(
                domain, "turn_off", {"entity_id": entity_id}, blocking=True
            )
        except HomeAssistantError as err:
            _LOGGER.error("Could not turn off %s: %s", entity_id, err)

    async def _async_wake(self) -> bool:
        """Wake the projector. Returns False if no wake method is configured.

        A linked entity goes first, because it's the user's own power control.
        It can't confirm the projector reacted, and a cold boot takes longer than
        we can block a service call, so we show "booting" and fall back to off if
        it never answers. Wake-on-LAN alone does get an answer, so it raises if
        there is none instead of a toggle that silently flips back to off.
        """
        options = self.config_entry.options
        mac = options.get(CONF_WOL_MAC) if wol_enabled(options) else None
        wake_entity = options.get(CONF_WAKE_ENTITY)
        if not mac and not wake_entity:
            return False
        generation = self._power_gen
        if wake_entity:
            await self._async_wake_via_entity(wake_entity)
            if generation == self._power_gen:
                self._async_start_waking()
            if mac:
                self.config_entry.async_create_background_task(
                    self.hass,
                    self._async_wake_on_lan(mac, generation),
                    "aetherion_wake_on_lan",
                )
            return True
        if await self._async_wake_on_lan(mac, generation):
            if generation == self._power_gen:
                self._async_start_waking()
            return True
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="wake_no_answer"
        )

    @callback
    def _async_start_waking(self) -> None:
        self._wake_until = self.hass.loop.time() + WAKE_GRACE_SECONDS
        self._failures = 0
        self.async_set_updated_data(
            AetherionData(
                power=POWER_WARMING,
                reachable=False,
                waking=True,
                inputs=self.data.inputs if self.data else [],
            )
        )

    async def _async_wake_on_lan(self, mac: str, generation: int) -> bool:
        """Wake the network, then send power-on where the projector answers.

        Verified on an Aetherion MAX in network-optimized standby: a magic
        packet to the Wi-Fi MAC brings Wi-Fi up in ~2 s with the projector
        still in standby (POWR=0); "%1POWR 1" to the Wi-Fi IP then boots it
        and Ethernet answers ~7 s later. After a longer sleep the projector can
        drop into deep standby with Wi-Fi off; then nothing answers.
        """
        wake_host = self.config_entry.options.get(CONF_WAKE_HOST)
        hosts = [h for h in (wake_host, self.client.host) if h]
        clients = [
            AetherionClient(host, self.client.port, self.client.password, timeout=2)
            for host in dict.fromkeys(hosts)
        ]
        for client in clients:
            client.connect_timeout = 0.8
        try:
            async with asyncio.timeout(WAKE_TIMEOUT_SECONDS):
                return await self._async_wake_loop(mac, wake_host, clients, generation)
        except TimeoutError:
            _LOGGER.info(
                "No answer to Wake-on-LAN within %s s; the projector is probably "
                "in deep standby (Wi-Fi off)",
                WAKE_TIMEOUT_SECONDS,
            )
            return False
        finally:
            for client in clients:
                await client.close()

    async def _async_wake_loop(
        self,
        mac: str,
        wake_host: str | None,
        clients: list[AetherionClient],
        generation: int,
    ) -> bool:
        """Repeat magic packets and probe until the projector answers."""
        next_packet = 0.0
        while generation == self._power_gen:
            if time.monotonic() >= next_packet:
                await self.hass.async_add_executor_job(
                    send_magic_packet, mac, wake_host or self.client.host
                )
                next_packet = time.monotonic() + 2
            for client in clients:
                try:
                    power = _int(await client.query(1, "POWR"))
                except AetherionConnectionError:
                    continue
                except AetherionError as err:
                    _LOGGER.debug("Wake via %s: %s", client.host, err)
                    continue
                if power == POWER_STANDBY:
                    if generation != self._power_gen:
                        return False  # turned off meanwhile
                    _LOGGER.debug("%s awake in standby, powering on", client.host)
                    await client.power_on()
                _LOGGER.debug("Projector woke via %s", client.host)
                return True
            await asyncio.sleep(0.5)
        return False

    async def _async_wake_via_entity(self, entity_id: str) -> None:
        """Ask another integration (e.g. Android TV Remote) to wake the projector."""
        domain = entity_id.split(".", 1)[0]
        registry_entry = er.async_get(self.hass).async_get(entity_id)
        if domain not in WAKE_DOMAINS or (
            registry_entry is not None
            and registry_entry.config_entry_id == self.config_entry.entry_id
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_wake_entity",
                translation_placeholders={"entity_id": entity_id},
            )
        _LOGGER.debug("Control port closed, waking via %s", entity_id)
        await self.hass.services.async_call(
            domain, WAKE_DOMAINS[domain], {"entity_id": entity_id}, blocking=True
        )

    # --- commands -------------------------------------------------------------

    async def async_run(self, coro: Any, **optimistic: Any) -> None:
        """Await a client call, translate errors, patch state, refresh."""
        try:
            await coro
        except AetherionAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except AetherionCommandError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=COMMAND_ERROR_KEYS.get(err.code, "command_failed"),
                translation_placeholders={"error": str(err)},
            ) from err
        except AetherionConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_command",
                translation_placeholders={"error": str(err)},
            ) from err
        if optimistic and self.data is not None:
            self.async_set_updated_data(replace(self.data, **optimistic))
        await self.async_request_refresh()

    @callback
    def async_handle_notification(self, command: str, data: str) -> None:
        """Apply a UDP status notification (%2POWR, %2INPT, %2ERST, %2LKUP)."""
        if self.data is None:
            return
        value = _int(data)
        if command == "POWR" and value is not None:
            # A notification proves the projector is reachable again.
            self._failures = 0
            self.async_set_updated_data(
                replace(self.data, power=value, reachable=True, waking=False)
            )
        elif command == "INPT" and value is not None:
            self.async_set_updated_data(replace(self.data, input=value))
        elif command == "ERST" and value is not None:
            self.async_set_updated_data(replace(self.data, error=value))
        self.hass.async_create_task(self.async_request_refresh())
