"""Constants for the AWOL Aetherion integration."""

from datetime import timedelta

DOMAIN = "aetherion"
MANUFACTURER = "AWOL Vision"

DEFAULT_NAME = "Aetherion"
SCAN_INTERVAL = timedelta(seconds=10)  # while on
OFF_SCAN_INTERVAL = timedelta(seconds=30)  # while off / unreachable
WAKING_SCAN_INTERVAL = timedelta(seconds=5)  # right after a wake request
OFF_CONNECT_TIMEOUT = 2.0
DISCOVERY_TIMEOUT = 2.0

DATA_LISTENER = "listener"

CONF_WAKE_ENTITY = "wake_entity"
# Turn the wake entity off again once the projector is confirmed fully off.
CONF_POWER_OFF_ENTITY = "power_off_entity"
CONF_POWER_OFF_DELAY = "power_off_delay"
# Cool-down between "confirmed off" and cutting power. AWOL doesn't document how
# long the laser/fans need, so this errs on the long side; it is configurable.
DEFAULT_POWER_OFF_DELAY = 120
MAX_POWER_OFF_DELAY = 900
# Give up (and leave the power on) if the projector never confirms it is off.
POWER_OFF_CONFIRM_TIMEOUT = 300
POWER_OFF_CHECK_SECONDS = 5
# Entities that can power the projector on, and the service to call on them.
WAKE_DOMAINS = {
    "button": "press",
    "input_boolean": "turn_on",
    "media_player": "turn_on",
    "remote": "turn_on",
    "script": "turn_on",
    "switch": "turn_on",
}
# Of those, the ones that have a meaningful "off".
POWER_OFF_DOMAINS = ("input_boolean", "media_player", "remote", "switch")
# Stable device key for entity/device IDs. Starts as the first host (the
# unique_id) and is kept when the entry is reconfigured to another address.
CONF_DEVICE_KEY = "device_key"
CONF_WOL_ENABLED = "wol_enabled"
CONF_WOL_MAC = "wol_mac"
# IP the projector answers on while in standby (its Wi-Fi interface when
# Ethernet is the main connection). Power-on is sent there after Wake-on-LAN.
CONF_WAKE_HOST = "wake_host"
# Wi-Fi answered within ~2.5 s in every successful wake; deep standby never answers.
WAKE_TIMEOUT_SECONDS = 15
# How long to report "on" after a wake request while the control port is still
# closed (the port opens 1-2 minutes after boot).
WAKE_GRACE_SECONDS = 180


def wol_enabled(options) -> bool:
    """Whether Wake-on-LAN is on. Off unless chosen.

    Entries saved before this option existed count as on only if they have a
    wake host, because only those were set up for it on purpose (a bare MAC may
    just be the old automatic fill-in).
    """
    return options.get(CONF_WOL_ENABLED, bool(options.get(CONF_WAKE_HOST)))


def device_key(entry) -> str:
    """Stable ID for this projector's device and entities."""
    return entry.data.get(CONF_DEVICE_KEY) or entry.unique_id or entry.entry_id
