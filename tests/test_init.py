"""Integration tests against the fake projector."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_INPUT_SOURCE_LIST,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    DOMAIN as MP_DOMAIN,
    MediaPlayerEntityFeature,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PORT,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.aetherion.const import DOMAIN

from .fake_projector import FakeProjector

MP = "media_player.aetherion"


async def _setup(hass: HomeAssistant, projector: FakeProjector) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Aetherion",
        unique_id="127.0.0.1",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: projector.port},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_setup_and_states(hass: HomeAssistant, projector: FakeProjector) -> None:
    entry = await _setup(hass, projector)
    assert entry.state is ConfigEntryState.LOADED

    state = hass.states.get(MP)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_INPUT_SOURCE] == "HDMI 1"
    assert state.attributes[ATTR_INPUT_SOURCE_LIST] == [
        "Google TV", "HDMI 1", "HDMI 2", "HDMI 3", "DP", "AV"
    ]
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.5
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is False
    assert state.attributes["app_name"] == "HDMI 1"
    assert state.attributes["media_title"] == "HDR10 · Dolby Atmos"
    assert state.attributes["entity_picture"].startswith("/api/media_player_proxy/")

    assert hass.states.get("select.aetherion_picture_mode").state == "Standard"
    assert hass.states.get("select.aetherion_motion_enhancement").state == "Standard"
    assert hass.states.get("select.aetherion_gamma").state == "2.2"
    assert hass.states.get("sensor.aetherion_laser_level").state == "8"
    assert hass.states.get("switch.aetherion_enhanced_black_level").state == STATE_OFF
    assert hass.states.get("sensor.aetherion_hdr_format").state == "HDR10"
    assert hass.states.get("sensor.aetherion_audio_format").state == "Dolby Atmos"
    assert hass.states.get("sensor.aetherion_picture_size").state == "16:9"
    assert hass.states.get("sensor.aetherion_temperature_2").state == "43"
    registry = er.async_get(hass)
    # Less common sensors exist but start hidden.
    assert hass.states.get("sensor.aetherion_fan_1_speed").state == "1500"
    assert registry.async_get("sensor.aetherion_fan_1_speed").hidden_by is not None
    assert registry.async_get("sensor.aetherion_color_depth").hidden_by is not None
    assert registry.async_get("sensor.aetherion_temperature_1").hidden_by is None
    # Slots reading 0 (not fitted on this model) get hidden.
    assert registry.async_get("sensor.aetherion_temperature_3").hidden_by is not None
    assert registry.async_get("sensor.aetherion_fan_7_speed").hidden_by is not None
    features = state.attributes["supported_features"]
    assert features & MediaPlayerEntityFeature.VOLUME_SET
    assert hass.states.get("binary_sensor.aetherion_problem").state == STATE_OFF
    assert hass.states.get("remote.aetherion").state == STATE_ON

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_media_player_services(hass: HomeAssistant, projector: FakeProjector) -> None:
    entry = await _setup(hass, projector)

    await hass.services.async_call(
        MP_DOMAIN, "select_source", {ATTR_ENTITY_ID: MP, ATTR_INPUT_SOURCE: "HDMI 3"}, blocking=True
    )
    assert projector.state["1INPT"] == "33"
    assert hass.states.get(MP).attributes[ATTR_INPUT_SOURCE] == "HDMI 3"

    await hass.services.async_call(
        MP_DOMAIN, "volume_set", {ATTR_ENTITY_ID: MP, ATTR_MEDIA_VOLUME_LEVEL: 0.3}, blocking=True
    )
    assert projector.state["3VOLM"] == "30"

    await hass.services.async_call(MP_DOMAIN, "volume_up", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert projector.state["3VOLM"] == "31"

    await hass.services.async_call(
        MP_DOMAIN, "volume_mute", {ATTR_ENTITY_ID: MP, ATTR_MEDIA_VOLUME_MUTED: True}, blocking=True
    )
    assert projector.state["1AVMT"] == "21"

    await hass.services.async_call(MP_DOMAIN, "turn_off", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert projector.state["1POWR"] == "0"
    await _refresh(hass, entry)
    assert hass.states.get(MP).state == STATE_OFF
    assert hass.states.get("select.aetherion_picture_mode").state == STATE_UNAVAILABLE

    await hass.services.async_call(MP_DOMAIN, "turn_on", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert projector.state["1POWR"] == "1"
    await _refresh(hass, entry)
    assert hass.states.get(MP).state == STATE_ON


async def test_picture_controls(hass: HomeAssistant, projector: FakeProjector) -> None:
    await _setup(hass, projector)

    await hass.services.async_call(
        "select", "select_option",
        {ATTR_ENTITY_ID: "select.aetherion_picture_mode", "option": "FILMMAKER MODE"},
        blocking=True,
    )
    assert projector.state["3PICT"] == "FILMMAKER MODE"

    await hass.services.async_call(
        "select", "select_option",
        {ATTR_ENTITY_ID: "select.aetherion_color_temperature", "option": "Warm 1"},
        blocking=True,
    )
    assert projector.state["3CLTP"] == "0"

    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: "switch.aetherion_enhanced_black_level"}, blocking=True
    )
    assert projector.state["3ENBL"] == "4"

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.aetherion_manual_focus"}, blocking=True
    )
    assert projector.received[-1] == "%3FCMN"

    await hass.services.async_call(
        "remote", "send_command",
        {ATTR_ENTITY_ID: "remote.aetherion", "command": ["up", "ok", "%3LASL 9"]},
        blocking=True,
    )
    assert "%3RCNC 0" in projector.received and "%3RCNC 4" in projector.received
    assert projector.state["3LASL"] == "9"

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "remote", "send_command",
            {ATTR_ENTITY_ID: "remote.aetherion", "command": "jump"},
            blocking=True,
        )


async def test_projector_error_is_surfaced(hass: HomeAssistant, projector: FakeProjector) -> None:
    entry = await _setup(hass, projector)
    projector.state["3GAMA"] = "4"  # ST2084 is reported in HDR but not settable
    await _refresh(hass, entry)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "select", "select_option",
            {ATTR_ENTITY_ID: "select.aetherion_gamma", "option": "ST2084"},
            blocking=True,
        )


async def test_unreachable_reports_off(hass: HomeAssistant, projector: FakeProjector) -> None:
    entry = await _setup(hass, projector)
    await projector.stop()
    await entry.runtime_data.client.close()
    await _refresh(hass, entry)  # first failure keeps last state
    assert hass.states.get(MP).state == STATE_ON
    await _refresh(hass, entry)
    assert hass.states.get(MP).state == STATE_OFF
    assert hass.states.get("binary_sensor.aetherion_problem").state == STATE_UNAVAILABLE


async def test_notification_updates_state(hass: HomeAssistant, projector: FakeProjector) -> None:
    entry = await _setup(hass, projector)
    entry.runtime_data.async_handle_notification("INPT", "34")
    assert hass.states.get(MP).attributes[ATTR_INPUT_SOURCE] == "DP"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()


async def test_turn_on_via_wake_entity(hass: HomeAssistant, projector: FakeProjector) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Aetherion",
        unique_id="127.0.0.1",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: projector.port},
        options={"wake_entity": "remote.android_tv"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await projector.stop()
    await entry.runtime_data.client.close()
    await _refresh(hass, entry)
    await _refresh(hass, entry)
    assert hass.states.get(MP).state == STATE_OFF

    calls = async_mock_service(hass, "remote", "turn_on")
    await hass.services.async_call(MP_DOMAIN, "turn_on", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert calls[0].data["entity_id"] == "remote.android_tv"
    assert hass.states.get(MP).state == STATE_ON
    assert hass.states.get("select.aetherion_picture_mode").state == STATE_UNAVAILABLE
    await _refresh(hass, entry)  # port still closed, but inside the boot grace period
    assert hass.states.get(MP).state == STATE_ON


async def test_setup_while_projector_off(hass: HomeAssistant, socket_enabled: None) -> None:
    """HA booting while the projector sleeps must still load, showing off."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Aetherion", unique_id="127.0.0.1",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: 1},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(MP).state == STATE_OFF
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(MP_DOMAIN, "turn_on", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert err.value.translation_key == "standby_port_closed"


async def test_wake_entity_cannot_be_self(hass: HomeAssistant, projector: FakeProjector) -> None:
    entry = await _setup(hass, projector)
    hass.config_entries.async_update_entry(entry, options={"wake_entity": MP})
    await projector.stop()
    await entry.runtime_data.client.close()
    await _refresh(hass, entry)
    await _refresh(hass, entry)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(MP_DOMAIN, "turn_on", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert err.value.translation_key == "invalid_wake_entity"


async def test_send_command_returns_response(hass: HomeAssistant, projector: FakeProjector) -> None:
    await _setup(hass, projector)
    result = await hass.services.async_call(
        DOMAIN, "send_command", {ATTR_ENTITY_ID: MP, "command": "%3PICT ?"},
        blocking=True, return_response=True,
    )
    assert result[MP] == {"command": "%3PICT ?", "response": "Standard"}


async def test_audio_passthrough_and_no_signal(
    hass: HomeAssistant, projector: FakeProjector
) -> None:
    """Real firmware behaviour: VOLM ERR3 with external audio, 0x0 with no signal."""
    projector.state.pop("3VOLM")  # fake answers ERR1 -> treated like unavailable
    projector.state["2IRES"] = "0 x 0"
    projector.state["3SINF"] = "0*0p@0Hz 0 255 3 0 0"
    await _setup(hass, projector)
    state = hass.states.get(MP)
    features = state.attributes["supported_features"]
    assert not features & MediaPlayerEntityFeature.VOLUME_SET
    assert features & MediaPlayerEntityFeature.VOLUME_STEP
    assert features & MediaPlayerEntityFeature.VOLUME_MUTE
    assert hass.states.get("sensor.aetherion_input_resolution").state == "No signal"
    assert hass.states.get("sensor.aetherion_signal_timing").state == "No signal"
    assert hass.states.get("sensor.aetherion_hdr_format").state == "unknown"


async def test_unavailable_command_message(hass: HomeAssistant, projector: FakeProjector) -> None:
    await _setup(hass, projector)
    projector.respond_override = {"3CLTP": "ERR3"}
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "select", "select_option",
            {ATTR_ENTITY_ID: "select.aetherion_color_temperature", "option": "Cool"},
            blocking=True,
        )
    assert err.value.translation_key == "command_unavailable"


async def test_polls_slowly_while_off(hass: HomeAssistant, projector: FakeProjector) -> None:
    entry = await _setup(hass, projector)
    coordinator = entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=10)
    await projector.stop()
    await coordinator.client.close()
    await _refresh(hass, entry)
    await _refresh(hass, entry)
    assert hass.states.get(MP).state == STATE_OFF
    assert coordinator.update_interval == timedelta(seconds=30)
    assert coordinator.client.connect_timeout == 2.0


async def test_turn_on_wake_on_lan(hass: HomeAssistant, projector: FakeProjector) -> None:
    """Real sequence: magic packet -> Wi-Fi answers in standby -> POWR 1 -> on."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Aetherion", unique_id="127.0.0.1",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: projector.port},
        options={"wol_mac": "aa:bb:cc:00:11:22", "wake_host": "127.0.0.1"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    port = projector.port
    await projector.stop()
    await entry.runtime_data.client.close()
    await _refresh(hass, entry)
    await _refresh(hass, entry)
    assert hass.states.get(MP).state == STATE_OFF

    # Deep standby: nothing answers -> clear error, entity stays off.
    with (
        patch("custom_components.aetherion.coordinator.send_magic_packet"),
        patch("custom_components.aetherion.coordinator.WAKE_TIMEOUT_SECONDS", 1),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(MP_DOMAIN, "turn_on", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert err.value.translation_key == "wake_no_answer"
    assert hass.states.get(MP).state == STATE_OFF

    woke = FakeProjector(port=port)  # network up after the magic packet, still standby
    woke.state["1POWR"] = "0"
    await woke.start()
    with patch("custom_components.aetherion.coordinator.send_magic_packet") as wol:
        await hass.services.async_call(MP_DOMAIN, "turn_on", {ATTR_ENTITY_ID: MP}, blocking=True)
        assert hass.states.get(MP).state == STATE_ON
        assert hass.states.get(MP).attributes["booting"] is True
        assert entry.runtime_data.update_interval == timedelta(seconds=5)
        try:
            wol.assert_called_with("aa:bb:cc:00:11:22", "127.0.0.1")
            assert woke.received.count("%1POWR 1") == 1
            await _refresh(hass, entry)
            assert hass.states.get(MP).state == STATE_ON
            assert "booting" not in hass.states.get(MP).attributes

            # Turning off right after must not be undone by the wake logic.
            await hass.services.async_call(MP_DOMAIN, "turn_off", {ATTR_ENTITY_ID: MP}, blocking=True)
            await _refresh(hass, entry)
            assert woke.received.count("%1POWR 1") == 1
            assert hass.states.get(MP).state == STATE_OFF
        finally:
            await entry.runtime_data.client.close()
            await woke.stop()


async def test_zero_sensor_unhidden_when_it_reports(
    hass: HomeAssistant, projector: FakeProjector
) -> None:
    projector.state["3TEMP"] = "22 0 0 0 0 0 0 0"
    entry = await _setup(hass, projector)
    registry = er.async_get(hass)
    sensor = "sensor.aetherion_temperature_2"
    assert registry.async_get(sensor).hidden_by is er.RegistryEntryHider.INTEGRATION
    projector.state["3TEMP"] = "22 43 0 0 0 0 0 0"
    entry.runtime_data._poll_count = 0  # force the slow poll that reads TEMP
    await _refresh(hass, entry)
    assert registry.async_get(sensor).hidden_by is None
    # Hidden-by-default slots are never revealed, and a user's own hide is kept.
    assert registry.async_get("sensor.aetherion_temperature_3").hidden_by is not None
    registry.async_update_entity(sensor, hidden_by=er.RegistryEntryHider.USER)
    projector.state["3TEMP"] = "22 50 0 0 0 0 0 0"
    entry.runtime_data._poll_count = 0
    await _refresh(hass, entry)
    assert registry.async_get(sensor).hidden_by is er.RegistryEntryHider.USER


async def test_stale_poll_does_not_overwrite_pushed_state(
    hass: HomeAssistant, projector: FakeProjector
) -> None:
    entry = await _setup(hass, projector)
    coordinator = entry.runtime_data
    original = coordinator._async_poll

    async def poll_with_notification():
        data = await original()  # snapshot says "on"
        coordinator.async_handle_notification("POWR", "0")  # newer: standby
        return data

    coordinator._async_poll = poll_with_notification
    projector.state["1POWR"] = "1"
    await coordinator.async_refresh()
    assert hass.states.get(MP).state == STATE_OFF
    coordinator._async_poll = original
    projector.state["1POWR"] = "0"
    await hass.async_block_till_done()


async def test_power_off_cancels_inflight_wake(
    hass: HomeAssistant, projector: FakeProjector
) -> None:
    entry = await _setup(hass, projector)
    coordinator = entry.runtime_data
    projector.state["1POWR"] = "0"  # answering, in standby
    generation = coordinator._power_gen
    coordinator._power_gen += 1  # a turn_off happened after the wake started
    with patch("custom_components.aetherion.coordinator.send_magic_packet"):
        assert not await coordinator._async_wake_on_lan("aa:bb:cc:00:11:22", generation)
    assert "%1POWR 1" not in projector.received


async def test_wake_deadline_is_enforced(hass: HomeAssistant, projector: FakeProjector) -> None:
    entry = await _setup(hass, projector)
    coordinator = entry.runtime_data
    projector.silent = True  # connects but never greets -> operations would hang
    await coordinator.client.close()
    loop = asyncio.get_running_loop()
    start = loop.time()
    with (
        patch("custom_components.aetherion.coordinator.send_magic_packet"),
        patch("custom_components.aetherion.coordinator.WAKE_TIMEOUT_SECONDS", 0.3),
    ):
        assert not await coordinator._async_wake_on_lan(
            "aa:bb:cc:00:11:22", coordinator._power_gen
        )
    assert loop.time() - start < 1.5
    projector.silent = False


# --- power control options ----------------------------------------------------

POWER = "switch.projector_power"
COORD = "custom_components.aetherion.coordinator"


async def _setup_with(hass, projector, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Aetherion", unique_id="127.0.0.1",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: projector.port},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _settle(hass) -> None:
    """Let the power-cut task run a few checks."""
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()


@pytest.fixture
def fast_power_checks():
    with (
        patch(f"{COORD}.POWER_OFF_CHECK_SECONDS", 0.01),
        patch(f"{COORD}.POWER_OFF_CONFIRM_TIMEOUT", 30),
    ):
        yield


async def _go_offline(hass, entry, projector) -> None:
    await projector.stop()
    await entry.runtime_data.client.close()
    await _refresh(hass, entry)
    await _refresh(hass, entry)
    assert hass.states.get(MP).state == STATE_OFF


async def test_power_cut_waits_for_projector_to_be_fully_off(
    hass: HomeAssistant, projector: FakeProjector, fast_power_checks
) -> None:
    entry = await _setup_with(
        hass, projector, wake_entity=POWER, power_off_entity=True, power_off_delay=0
    )
    calls = async_mock_service(hass, "switch", "turn_off")
    await hass.services.async_call(MP_DOMAIN, "turn_off", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert projector.state["1POWR"] == "0"

    # Standby reported but the port is still open: too early to cut.
    await _settle(hass)
    assert not calls

    await projector.stop()  # the port closes a few seconds into standby
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [c.data["entity_id"] for c in calls] == [POWER]
    await entry.runtime_data.client.close()


async def test_power_cut_honours_cool_down(
    hass: HomeAssistant, projector: FakeProjector, fast_power_checks
) -> None:
    await _setup_with(
        hass, projector, wake_entity=POWER, power_off_entity=True, power_off_delay=60
    )
    calls = async_mock_service(hass, "switch", "turn_off")
    await hass.services.async_call(MP_DOMAIN, "turn_off", {ATTR_ENTITY_ID: MP}, blocking=True)
    await projector.stop()
    await _settle(hass)
    assert not calls  # confirmed off, still inside the 60 s cool-down


async def test_power_left_on_when_off_is_never_confirmed(
    hass: HomeAssistant, projector: FakeProjector
) -> None:
    await _setup_with(
        hass, projector, wake_entity=POWER, power_off_entity=True, power_off_delay=0
    )
    calls = async_mock_service(hass, "switch", "turn_off")
    projector.respond_override = {"1POWR": "OK"}  # accepted, but it stays on
    with (
        patch(f"{COORD}.POWER_OFF_CHECK_SECONDS", 0.01),
        patch(f"{COORD}.POWER_OFF_CONFIRM_TIMEOUT", 0.05),
    ):
        await hass.services.async_call(MP_DOMAIN, "turn_off", {ATTR_ENTITY_ID: MP}, blocking=True)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert projector.state["1POWR"] == "1"
    assert not calls


async def test_power_left_on_if_turned_back_on_during_cool_down(
    hass: HomeAssistant, projector: FakeProjector, fast_power_checks
) -> None:
    entry = await _setup_with(
        hass, projector, wake_entity=POWER, power_off_entity=True, power_off_delay=60
    )
    calls = async_mock_service(hass, "switch", "turn_off")
    port = projector.port
    await hass.services.async_call(MP_DOMAIN, "turn_off", {ATTR_ENTITY_ID: MP}, blocking=True)
    await projector.stop()
    await _settle(hass)

    back_on = FakeProjector(port=port)  # someone used the remote
    await back_on.start()
    try:
        await hass.async_block_till_done(wait_background_tasks=True)
        assert not calls
    finally:
        await entry.runtime_data.client.close()
        await back_on.stop()


async def test_turn_on_cancels_pending_power_cut(
    hass: HomeAssistant, projector: FakeProjector, fast_power_checks
) -> None:
    await _setup_with(
        hass, projector, wake_entity=POWER, power_off_entity=True, power_off_delay=60
    )
    calls = async_mock_service(hass, "switch", "turn_off")
    async_mock_service(hass, "switch", "turn_on")
    await hass.services.async_call(MP_DOMAIN, "turn_off", {ATTR_ENTITY_ID: MP}, blocking=True)
    await projector.stop()
    await _settle(hass)
    await hass.services.async_call(MP_DOMAIN, "turn_on", {ATTR_ENTITY_ID: MP}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not calls


async def test_power_not_cut_when_off_command_refused(
    hass: HomeAssistant, projector: FakeProjector, fast_power_checks
) -> None:
    await _setup_with(
        hass, projector, wake_entity=POWER, power_off_entity=True, power_off_delay=0
    )
    calls = async_mock_service(hass, "switch", "turn_off")
    projector.respond_override = {"1POWR": "ERR3"}
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(MP_DOMAIN, "turn_off", {ATTR_ENTITY_ID: MP}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not calls


async def test_power_not_cut_when_projector_unreachable(
    hass: HomeAssistant, projector: FakeProjector, fast_power_checks
) -> None:
    """Could be off, could be on with the network down: never cut blind."""
    entry = await _setup_with(
        hass, projector, wake_entity=POWER, power_off_entity=True, power_off_delay=0
    )
    calls = async_mock_service(hass, "switch", "turn_off")
    await _go_offline(hass, entry, projector)
    await hass.services.async_call(MP_DOMAIN, "turn_off", {ATTR_ENTITY_ID: MP}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not calls


async def test_power_entity_not_turned_off_unless_chosen(
    hass: HomeAssistant, projector: FakeProjector, fast_power_checks
) -> None:
    await _setup_with(hass, projector, wake_entity=POWER, power_off_delay=0)
    calls = async_mock_service(hass, "switch", "turn_off")
    await hass.services.async_call(MP_DOMAIN, "turn_off", {ATTR_ENTITY_ID: MP}, blocking=True)
    await projector.stop()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not calls


async def test_remote_power_off_key_uses_the_same_path(
    hass: HomeAssistant, projector: FakeProjector, fast_power_checks
) -> None:
    entry = await _setup_with(
        hass, projector, wake_entity=POWER, power_off_entity=True, power_off_delay=0
    )
    calls = async_mock_service(hass, "switch", "turn_off")
    await hass.services.async_call(
        "remote", "send_command",
        {ATTR_ENTITY_ID: "remote.aetherion", "command": "power_off"},
        blocking=True,
    )
    await projector.stop()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 1
    await entry.runtime_data.client.close()


@pytest.mark.parametrize(
    "options",
    [
        {"wol_mac": "aa:bb:cc:00:11:22"},  # MAC alone never enables it
        {"wol_mac": "aa:bb:cc:00:11:22", "wol_enabled": False},
        {"wol_mac": "aa:bb:cc:00:11:22", "wake_host": "127.0.0.1", "wol_enabled": False},
    ],
)
async def test_no_magic_packet_unless_enabled(
    hass: HomeAssistant, projector: FakeProjector, options
) -> None:
    entry = await _setup_with(hass, projector, **options)
    await _go_offline(hass, entry, projector)
    with (
        patch(f"{COORD}.send_magic_packet") as wol,
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(MP_DOMAIN, "turn_on", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert err.value.translation_key == "standby_port_closed"
    wol.assert_not_called()


async def test_magic_packet_when_enabled(hass: HomeAssistant, projector: FakeProjector) -> None:
    entry = await _setup_with(
        hass, projector, wol_enabled=True, wol_mac="aa:bb:cc:00:11:22"
    )
    await _go_offline(hass, entry, projector)
    with (
        patch(f"{COORD}.send_magic_packet") as wol,
        patch(f"{COORD}.WAKE_TIMEOUT_SECONDS", 0.2),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(MP_DOMAIN, "turn_on", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert err.value.translation_key == "wake_no_answer"
    wol.assert_called()


async def test_power_entity_used_first_when_both_set(
    hass: HomeAssistant, projector: FakeProjector
) -> None:
    entry = await _setup_with(
        hass, projector, wake_entity=POWER, wol_enabled=True, wol_mac="aa:bb:cc:00:11:22"
    )
    await _go_offline(hass, entry, projector)
    calls = async_mock_service(hass, "switch", "turn_on")
    with (
        patch(f"{COORD}.send_magic_packet") as wol,
        patch(f"{COORD}.WAKE_TIMEOUT_SECONDS", 0.2),
    ):
        await hass.services.async_call(MP_DOMAIN, "turn_on", {ATTR_ENTITY_ID: MP}, blocking=True)
        assert [c.data["entity_id"] for c in calls] == [POWER]
        assert hass.states.get(MP).attributes["booting"] is True
        await hass.async_block_till_done(wait_background_tasks=True)
    wol.assert_called()
