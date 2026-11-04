"""Config flow tests."""

from __future__ import annotations

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.aetherion.const import DOMAIN
from custom_components.aetherion.protocol import DiscoveredProjector

from .fake_projector import FakeProjector

SETUP = "custom_components.aetherion.async_setup_entry"


async def test_manual_flow(hass: HomeAssistant, projector: FakeProjector) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"

    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "127.0.0.1", CONF_PORT: projector.port},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options == {}  # Wake-on-LAN is opt-in
    # Generic device name "Aetherion" -> titled by model to avoid entity ID clashes
    assert result["title"] == "Aetherion Max"
    assert result["data"] == {CONF_HOST: "127.0.0.1", CONF_PORT: projector.port}
    assert result["result"].unique_id == "127.0.0.1"


async def test_manual_errors(hass: HomeAssistant, socket_enabled: None) -> None:
    fake = FakeProjector(password="AWOLLink")
    await fake.start()
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1}
        )
        assert result["errors"] == {"base": "cannot_connect"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port}
        )
        assert result["errors"] == {"base": "invalid_auth"}

        with patch(SETUP, return_value=True):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port, CONF_PASSWORD: "AWOLLink"},
            )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_PASSWORD] == "AWOLLink"
    finally:
        await fake.stop()


async def test_discovery_pick(hass: HomeAssistant, projector: FakeProjector) -> None:
    found = [DiscoveredProjector("127.0.0.1", "Aetherion Max", "Living Room")]
    with patch(
        "custom_components.aetherion.config_flow.AetherionConfigFlow._async_discover",
        return_value=found,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["step_id"] == "pick"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": "127.0.0.1"}
    )
    assert result["step_id"] == "manual"
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: projector.port}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(hass: HomeAssistant, projector: FakeProjector) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id="127.0.0.1", data={CONF_HOST: "127.0.0.1"}
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: projector.port}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _options_submit(hass, entry, user_input):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.options.async_configure(result["flow_id"], user_input)


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="x", data={CONF_HOST: "10.0.0.9"})
    entry.add_to_hass(hass)

    result = await _options_submit(hass, entry, {"wol_mac": "bad"})
    assert result["errors"] == {"wol_mac": "invalid_mac"}
    result = await _options_submit(hass, entry, {"wol_enabled": True})
    assert result["errors"] == {"wol_mac": "mac_required"}
    result = await _options_submit(hass, entry, {"power_off_entity": True})
    assert result["errors"] == {"wake_entity": "power_entity_required"}
    result = await _options_submit(
        hass, entry, {"power_off_entity": True, "wake_entity": "script.projector_ir_on"}
    )
    assert result["errors"] == {"wake_entity": "entity_cannot_turn_off"}

    result = await _options_submit(
        hass,
        entry,
        {
            "wol_enabled": True,
            "wol_mac": "AA-BB-CC-00-11-22",
            "wake_entity": "switch.projector_power",
            "power_off_entity": True,
            "power_off_delay": 60.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "wol_enabled": True,
        "wol_mac": "aa:bb:cc:00:11:22",
        "wake_entity": "switch.projector_power",
        "power_off_entity": True,
        "power_off_delay": 60,
    }


async def test_options_defaults_and_unticking(hass: HomeAssistant) -> None:
    """Wake-on-LAN defaults off, and an unticked box stays off."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="x",
        data={CONF_HOST: "10.0.0.9"},
        options={"wol_mac": "aa:bb:cc:00:11:22", "wake_host": "10.0.0.8"},
    )
    entry.add_to_hass(hass)
    result = await _options_submit(
        hass, entry, {"wol_enabled": False, "wol_mac": "aa:bb:cc:00:11:22", "wake_host": "10.0.0.8"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["wol_enabled"] is False
    assert entry.options["power_off_entity"] is False
    assert entry.options["power_off_delay"] == 120


async def test_reconfigure_keeps_ids_and_frees_old_address(
    hass: HomeAssistant, projector: FakeProjector
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="127.0.0.1",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: projector.port},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with (
        patch(SETUP, return_value=True),
        patch(
            "custom_components.aetherion.config_flow.validate_connection",
            return_value={"name": "Aetherion MAX", "model": "Aetherion MAX"},
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.20", CONF_PORT: projector.port}
        )
    assert result.get("reason") == "reconfigure_successful", result
    assert entry.unique_id == "10.0.0.20"
    assert entry.data["device_key"] == "127.0.0.1"  # entity/device IDs unchanged

    # The old address can now be added as a new projector without touching
    # the existing entry.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: projector.port}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_HOST] == "10.0.0.20"
