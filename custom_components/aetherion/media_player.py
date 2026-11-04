"""Media player entity for the AWOL Aetherion projector."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import AetherionConfigEntry
from .entity import AetherionEntity
from .protocol import INPUTS

PARALLEL_UPDATES = 1

SOURCE_TO_CODE = {name: code for code, name in INPUTS.items()}
SOURCE_TO_CODE["Home"] = 30  # name used by the AWOL spec
# The protocol has no now-playing artwork; show the brand logo instead of the
# frontend's music-note placeholder.
ARTWORK = Path(__file__).parent / "brand" / "icon@2x.png"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AetherionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the media player."""
    async_add_entities([AetherionMediaPlayer(entry.runtime_data)])
    entity_platform.async_get_current_platform().async_register_entity_service(
        "send_command",
        {vol.Required("command"): cv.string},
        "async_send_protocol_command",
        supports_response=SupportsResponse.OPTIONAL,
    )


class AetherionMediaPlayer(AetherionEntity, MediaPlayerEntity):
    """The projector as a media player."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_translation_key = "projector"
    _attr_media_image_hash = "awol-logo"
    _requires_power = False

    def __init__(self, coordinator) -> None:
        """Init."""
        super().__init__(coordinator, "media_player")

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Features that apply right now.

        Source and volume controls only while on. The volume slider is only
        offered when the built-in speaker is in use: with audio passthrough
        (eARC/optical/BT) %3VOLM answers ERR3, while volume step and mute still
        work and are forwarded to the external device.
        """
        data = self.coordinator.data
        features = MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF
        if data.controllable:
            features |= (
                MediaPlayerEntityFeature.SELECT_SOURCE
                | MediaPlayerEntityFeature.VOLUME_STEP
                | MediaPlayerEntityFeature.VOLUME_MUTE
            )
            if data.volume is not None:
                features |= MediaPlayerEntityFeature.VOLUME_SET
        return features

    @property
    def state(self) -> MediaPlayerState:
        """On or off."""
        return MediaPlayerState.ON if self.coordinator.data.is_on else MediaPlayerState.OFF

    @property
    def source_list(self) -> list[str]:
        """Inputs reported by %1INST, or all known inputs."""
        codes = self.coordinator.data.inputs or list(INPUTS)
        return [INPUTS.get(code, f"Input {code}") for code in codes]

    @property
    def source(self) -> str | None:
        """Current input."""
        code = self.coordinator.data.input
        if code is None or not self.coordinator.data.controllable:
            return None
        return INPUTS.get(code, f"Input {code}")

    @property
    def app_name(self) -> str | None:
        """Shown under the title in the media dialog: the current input."""
        return self.source

    @property
    def media_title(self) -> str | None:
        """Signal summary, e.g. ``Dolby Vision · Dolby Atmos``."""
        data = self.coordinator.data
        if not data.controllable or data.signal is None:
            return None
        parts = [
            p
            for p in (data.signal.hdr_format, data.signal.audio_format)
            if p
        ]
        return " · ".join(parts) or None

    @property
    def entity_picture(self) -> str | None:
        """Logo only while on (off/unavailable keep the plain state view)."""
        return super().entity_picture if self.coordinator.data.controllable else None

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Serve the brand logo as artwork."""
        data = await self.hass.async_add_executor_job(ARTWORK.read_bytes)
        return data, "image/png"

    @property
    def volume_level(self) -> float | None:
        """Volume 0..1."""
        vol = self.coordinator.data.volume
        return None if vol is None else vol / 100

    @property
    def is_volume_muted(self) -> bool | None:
        """Muted state."""
        return self.coordinator.data.muted

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Flag the boot window after a wake request."""
        return {"booting": True} if self.coordinator.data.waking else {}

    async def async_send_protocol_command(self, command: str) -> ServiceResponse:
        """Send a raw frame (``%3PICT ?``) and return the projector's answer."""
        result: dict[str, str] = {}

        async def _send() -> None:
            result["response"] = await self.coordinator.client.send_raw(command)

        await self.coordinator.async_run(_send())
        return {"command": command, **result}

    async def async_turn_on(self) -> None:
        """Power on."""
        await self.coordinator.async_turn_on()

    async def async_turn_off(self) -> None:
        """Standby."""
        await self.coordinator.async_turn_off()

    async def async_select_source(self, source: str) -> None:
        """Switch input."""
        code = SOURCE_TO_CODE.get(source)
        if code is None and source.lower().startswith("input "):
            code = int(source[6:]) if source[6:].isdigit() else None
        if code is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_source",
                translation_placeholders={
                    "source": source,
                    "options": ", ".join(self.source_list),
                },
            )
        await self.coordinator.async_run(
            self.coordinator.client.select_input(code), input=code
        )

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume (only works when audio output is the built-in speaker)."""
        level = round(volume * 100)
        await self.coordinator.async_run(
            self.coordinator.client.set_volume(level), volume=level
        )

    async def async_volume_up(self) -> None:
        """Volume up."""
        await self.coordinator.async_run(self.coordinator.client.volume_step(True))

    async def async_volume_down(self) -> None:
        """Volume down."""
        await self.coordinator.async_run(self.coordinator.client.volume_step(False))

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute."""
        await self.coordinator.async_run(
            self.coordinator.client.set_mute(mute), muted=mute
        )
