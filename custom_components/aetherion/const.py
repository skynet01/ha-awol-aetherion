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
# Stable device key for entity/device IDs. Starts as the first host (the
# unique_id) and is kept when the entry is reconfigured to another address.
CONF_DEVICE_KEY = "device_key"
CONF_WOL_MAC = "wol_mac"
# IP the projector answers on while in standby (its Wi-Fi interface when
# Ethernet is the main connection). Power-on is sent there after Wake-on-LAN.
CONF_WAKE_HOST = "wake_host"
# Wi-Fi answered within ~2.5 s in every successful wake; deep standby never answers.
WAKE_TIMEOUT_SECONDS = 15
# How long to report "on" after a wake request while the control port is still
# closed (the port opens 1-2 minutes after boot).
WAKE_GRACE_SECONDS = 180


def device_key(entry) -> str:
    """Stable ID for this projector's device and entities."""
    return entry.data.get(CONF_DEVICE_KEY) or entry.unique_id or entry.entry_id
