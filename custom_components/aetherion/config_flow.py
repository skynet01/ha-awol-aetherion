"""Config flow for the AWOL Aetherion projector."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_DEVICE_KEY,
    CONF_WAKE_ENTITY,
    CONF_WAKE_HOST,
    CONF_WOL_MAC,
    DEFAULT_NAME,
    DISCOVERY_TIMEOUT,
    DOMAIN,
    device_key,
)
from .protocol import (
    DEFAULT_PORT,
    AetherionAuthError,
    AetherionClient,
    AetherionError,
    DiscoveredProjector,
    arp_lookup,
    normalize_mac,
)

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE = "device"
MANUAL = "manual"


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate bad credentials."""


async def validate_connection(data: Mapping[str, Any]) -> dict[str, str | None]:
    """Connect and read the projector name/model."""
    client = AetherionClient(
        data[CONF_HOST], data.get(CONF_PORT, DEFAULT_PORT), data.get(CONF_PASSWORD)
    )
    try:
        await client.query(1, "POWR")
        name = await client.try_query(1, "NAME")
        model = await client.try_query(1, "INF2")
    except AetherionAuthError as err:
        raise InvalidAuth from err
    except AetherionError as err:
        _LOGGER.debug("Validation failed: %s", err)
        raise CannotConnect from err
    finally:
        await client.close()
    name = (name or "").strip()
    model = (model or "").strip()
    # The default device name is a generic "Projector"; prefer the model so
    # entity IDs don't collide with other projector integrations.
    if not name or name.lower() in ("projector", "aetherion"):
        name = model or DEFAULT_NAME
    return {"name": name, "model": model}


def _manual_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Optional(
                CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional(
                CONF_PASSWORD,
                description={"suggested_value": defaults.get(CONF_PASSWORD)},
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        }
    )


class AetherionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options flow."""
        return AetherionOptionsFlow()

    def __init__(self) -> None:
        """Init."""
        self._discovered: dict[str, DiscoveredProjector] = {}
        self._defaults: dict[str, Any] = {}

    async def _async_discover(self) -> list[DiscoveredProjector]:
        # Imported here so tests can patch it on the package.
        from . import async_get_listener  # noqa: PLC0415

        listener = await async_get_listener(self.hass)
        if listener is None:
            return []
        return await listener.search(DISCOVERY_TIMEOUT)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Search the LAN, then offer found projectors or manual entry."""
        configured = {
            entry.data.get(CONF_HOST) for entry in self._async_current_entries()
        }
        found = await self._async_discover()
        self._discovered = {p.host: p for p in found if p.host not in configured}
        if not self._discovered:
            return await self.async_step_manual()
        return await self.async_step_pick()

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a discovered projector."""
        if user_input is not None:
            choice = user_input[CONF_DEVICE]
            if choice != MANUAL:
                self._defaults = {CONF_HOST: choice}
            return await self.async_step_manual()

        options = [
            SelectOptionDict(value=p.host, label=f"{p.name} ({p.model}, {p.host})")
            for p in self._discovered.values()
        ]
        options.append(SelectOptionDict(value=MANUAL, label="Enter address manually"))
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE): SelectSelector(SelectSelectorConfig(options=options))}
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Host / password entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, info = await self._async_validate(user_input, errors)
            if info is not None:
                await self.async_set_unique_id(data[CONF_HOST])
                # No auto host update: the unique_id is an address, not an
                # identity, and could belong to a different projector by now.
                self._abort_if_unique_id_configured()
                self._async_abort_entries_match({CONF_HOST: data[CONF_HOST]})
                # Connecting just populated HA's ARP table: grab the MAC so
                # Wake-on-LAN works without the user looking it up.
                mac = await self.hass.async_add_executor_job(arp_lookup, data[CONF_HOST])
                return self.async_create_entry(
                    title=info["name"],
                    data=data,
                    options={CONF_WOL_MAC: mac} if mac else {},
                )
            self._defaults = user_input
        return self.async_show_form(
            step_id="manual", data_schema=_manual_schema(self._defaults), errors=errors
        )

    async def _async_validate(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> tuple[dict[str, Any], dict[str, str | None] | None]:
        data = {
            CONF_HOST: user_input[CONF_HOST].strip(),
            CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
        }
        if user_input.get(CONF_PASSWORD):
            data[CONF_PASSWORD] = user_input[CONF_PASSWORD]
        try:
            info = await validate_connection(data)
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error")
            errors["base"] = "unknown"
        else:
            return data, info
        return data, None

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host, port, password or MAC."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if any(
                host in (other.data.get(CONF_HOST), other.unique_id)
                for other in self._async_current_entries(include_ignore=False)
                if other.entry_id != entry.entry_id
            ):
                errors["base"] = "already_configured"
            else:
                data, info = await self._async_validate(user_input, errors)
                if info is not None:
                    # Keep entity/device IDs, but move the unique_id to the new
                    # address so the old address is free for another projector.
                    data[CONF_DEVICE_KEY] = device_key(entry)
                    return self.async_update_reload_and_abort(
                        entry, unique_id=host, data=data
                    )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_manual_schema(user_input or entry.data),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await validate_connection(data)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )


class AetherionOptionsFlow(OptionsFlow):
    """Pick an entity that can wake the projector from standby."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options = {k: v for k, v in user_input.items() if v}
            try:
                if CONF_WOL_MAC in options:
                    options[CONF_WOL_MAC] = normalize_mac(options[CONF_WOL_MAC])
            except ValueError:
                errors[CONF_WOL_MAC] = "invalid_mac"
            else:
                return self.async_create_entry(data=options)
        current = self.config_entry.options.get(CONF_WAKE_ENTITY)
        current_mac = self.config_entry.options.get(CONF_WOL_MAC)
        current_wake_host = self.config_entry.options.get(CONF_WAKE_HOST)
        own = [
            e.entity_id
            for e in er.async_entries_for_config_entry(
                er.async_get(self.hass), self.config_entry.entry_id
            )
        ]
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_WOL_MAC, description={"suggested_value": current_mac}
                    ): str,
                    vol.Optional(
                        CONF_WAKE_HOST,
                        description={"suggested_value": current_wake_host},
                    ): str,
                    vol.Optional(
                        CONF_WAKE_ENTITY, description={"suggested_value": current}
                    ): EntitySelector(
                        EntitySelectorConfig(
                            domain=["remote", "media_player", "script", "switch", "button"],
                            exclude_entities=own,
                        )
                    )
                }
            ),
        )
