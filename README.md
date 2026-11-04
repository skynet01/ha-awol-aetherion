# AWOL Aetherion Projector for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Validate](https://github.com/skynet01/ha-awol-aetherion/actions/workflows/validate.yml/badge.svg)](https://github.com/skynet01/ha-awol-aetherion/actions/workflows/validate.yml)

Local network control for **AWOL Vision Aetherion** projectors (Max, Pro, Plus), using
AWOL's official IP control protocol (PJLink with AWOL extensions, TCP/UDP port 4352).
No cloud and no ADB.

## Features

- **Media player**: power, input (Google TV, HDMI 1-3, DP, AV), volume step and mute.
  The volume slider only appears when the built-in speaker is in use.
- **Remote**: navigation keys (up, down, left, right, ok, back, home, menu, settings),
  usable from remote cards and `remote.send_command`.
- **Picture settings**: picture mode (follows SDR, HDR, HDR10+ and Dolby Vision),
  motion enhancement, gamma, dynamic tone mapping, color temperature, enhanced black
  level, and a manual focus button.
- **Sensors**: input resolution, signal timing, HDR and audio format, picture size,
  laser level, temperatures, fan speeds, and a problem alert.
- **Power on from standby** via Wake-on-LAN (see below).
- **`aetherion.send_command` action**: send any raw protocol command and get the reply.
- LAN discovery, instant status updates, and gentle polling while the projector is off.

## Requirements

- Aetherion firmware **20260929** or newer.
- On the projector, enable **Network control**.
- To power on from Home Assistant, also enable **network optimized standby**.

## Installation

**HACS (recommended)**

1. In HACS, open the three-dot menu, choose **Custom repositories**, and add
   `https://github.com/skynet01/ha-awol-aetherion` with type **Integration**.
2. Install **AWOL Aetherion Projector**, then restart Home Assistant.

**Manual**: copy `custom_components/aetherion` into your `config/custom_components/`
folder, then restart Home Assistant.

## Setup

1. Turn the projector **on**.
2. Go to **Settings > Devices & services > Add integration** and choose
   **AWOL Aetherion Projector**.
3. Pick the projector found on your network, or enter its IP address. A password is
   only needed if you set one for network control on the projector.

Give the projector's IP address(es) a DHCP reservation in your router.

## Powering on from standby

In standby the projector closes its control port, so the regular power-on command
can't reach it. With **network optimized standby** enabled, the integration wakes it
like this:

1. It sends a Wake-on-LAN packet to the projector's **Wi-Fi** MAC address.
2. Wi-Fi comes up in about 2 seconds, and the integration sends power-on.
3. The projector is fully on in about 10 seconds.

Set both values under the integration's **Configure** (options):

| Option | Value |
| --- | --- |
| Wi-Fi MAC address | MAC of the projector's Wi-Fi interface (Ethernet's MAC does not wake it) |
| Wi-Fi IP address | Only needed if you set the projector up on its Ethernet IP |
| Wake entity | Optional extra wake method, for example an IR blaster script |

**Known limitation:** after the projector has been in standby for a while (about 2
hours in testing), it goes into deep standby and stops answering Wake-on-LAN. Then
only the remote, or an IR blaster set as the wake entity, can turn it on. If that
happens, `turn_on` shows an error instead of failing silently.

## Notes

- Laser level is read-only, because the projector refuses changes while dynamic iris
  is on.
- Less common sensors (fans, color details, extra temperature slots) start hidden.
  Temperature and fan slots your model doesn't have hide themselves automatically.
- Settings that don't apply to the current signal show as unavailable, for example
  tone mapping in SDR.

## Development

```bash
uv venv -p 3.14 .venv && uv pip install -p .venv pytest-homeassistant-custom-component
.venv/bin/python -m pytest
```

`tests/fake_projector.py` emulates the projector's TCP protocol, including
authentication.

Not affiliated with AWOL Vision.
