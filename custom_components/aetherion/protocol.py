"""Async client for the AWOL Aetherion control protocol.

The protocol is PJLink (class 1 and 2) extended with AWOL "class 3" commands.
This module has no Home Assistant dependencies so it can be tested on its own.

Frame format: ``%<class><CMD> <param>\\r`` -> ``%<class><CMD>=<data>\\r``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 4352
DEFAULT_TIMEOUT = 5.0
# The projector drops sessions after ~30 s without traffic; reconnect a bit early.
IDLE_RECONNECT = 20.0
MAX_PARAM_LEN = 128


class AetherionError(Exception):
    """Base error."""


class AetherionConnectionError(AetherionError):
    """Cannot reach the projector or the connection dropped."""


class AetherionAuthError(AetherionError):
    """Password missing or wrong."""


class AetherionCommandError(AetherionError):
    """Projector answered with ERR1..ERR4."""

    def __init__(self, command: str, code: str) -> None:
        """Init."""
        self.command = command
        self.code = code
        super().__init__(f"{command} failed: {code} ({ERROR_TEXT.get(code, 'unknown')})")


ERROR_TEXT = {
    "ERR1": "undefined command",
    "ERR2": "parameter out of range",
    "ERR3": "unavailable at this time",
    "ERR4": "projector failure",
}

# --- Value tables (from the AWOL command set) ---------------------------------

POWER_STANDBY = 0
POWER_ON = 1
POWER_COOLING = 2  # standard PJLink, not listed by AWOL but handled defensively
POWER_WARMING = 3

INPUTS: dict[int, str] = {
    30: "Google TV",  # the spec calls it "Home"; it is the built-in launcher
    31: "HDMI 1",
    32: "HDMI 2",
    33: "HDMI 3",
    34: "DP",
    35: "AV",
}

REMOTE_KEYS: dict[str, int] = {
    "up": 0,
    "down": 1,
    "left": 2,
    "right": 3,
    "ok": 4,
    "back": 5,
    "menu": 6,
    "settings": 7,
    "home": 8,
}
REMOTE_KEY_ALIASES = {"select": "ok", "enter": "ok", "return": "back"}

MOTION_MODES = {0: "Off", 1: "Custom", 2: "Film", 3: "Clear", 4: "Standard", 5: "Smooth"}
GAMMA_MODES = {0: "2.0", 1: "2.2", 2: "2.4", 3: "BT1886", 4: "ST2084", 5: "HLG"}
GAMMA_SETTABLE = (0, 1, 2, 3)
TONE_MAPPING_MODES = {0: "Off", 1: "Low", 2: "High"}
COLOR_TEMPERATURES = {0: "Warm 1", 1: "Warm 2", 2: "Standard", 3: "Cool"}
ASPECT_RATIOS = {
    0: "Auto",
    1: "4:3",
    2: "Zoom",
    3: "16:9",
    4: "21:9",
    5: "32:9",
    6: "Anamorphic 16:9",
    7: "Anamorphic 2.39:1",
    8: "Anamorphic 1.85:1",
    9: "Anamorphic 2.0:1",
}
ERROR_STATES = {
    0: "Normal",
    1: "Over temperature",
    2: "Open circuit",
    3: "Fan error",
    4: "Startup error",
    5: "Lens cover timeout",
    6: "Foreign object detection lost",
}
AUDIO_FORMATS = {
    0: "PCM",
    1: "AAC",
    2: "HE-AAC",
    3: "Dolby Audio",
    4: "Dolby Atmos",
    5: "DTS",
    6: "DTS-HD",
    7: "DTS:X",
    8: "MP3",
    9: "FLAC",
    10: "WMA",
    11: "MPEG",
    12: "Other",
    13: "Multi",
}
COLOR_DEPTHS = {0: "6-bit", 1: "8-bit", 2: "10-bit", 3: "12-bit", 4: "16-bit"}
COLOR_FORMATS = {0: "YUV 4:2:0", 1: "YUV 4:2:2", 2: "YUV 4:4:4", 3: "RGB"}
HDR_FORMATS = {0: "SDR", 1: "HDR10", 2: "HLG", 3: "HDR10+", 4: "Dolby Vision"}
COLOR_PRIMARIES = {0: "BT.709", 1: "DCI-P3", 2: "BT.2020"}
UNAVAILABLE_CODE = 255

TEMPERATURE_SENSOR_COUNT = 8
FAN_COUNT = 7


def _lookup(table: dict[int, str], raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        code = int(raw)
    except ValueError:
        return None
    if code == UNAVAILABLE_CODE:
        return None
    return table.get(code)


@dataclass(slots=True)
class SignalInfo:
    """Parsed %3SINF response."""

    timing: str | None = None
    audio_format: str | None = None
    color_depth: str | None = None
    color_format: str | None = None
    hdr_format: str | None = None
    color_primaries: str | None = None

    @classmethod
    def parse(cls, raw: str) -> SignalInfo:
        """Parse ``3840x2160@60Hz 4 1 3 1 2``."""
        parts = raw.split()
        get = lambda i: parts[i] if len(parts) > i else None  # noqa: E731
        timing = get(0)
        # No input signal (e.g. the built-in Google TV "Home" source) is reported
        # as ``0*0p@0Hz 0 255 3 0 0``; the codes after it are meaningless then.
        if not timing or timing.startswith(("0*0", "0x0", "-")) or timing in ("0", "255"):
            return cls()
        return cls(
            timing=timing.replace("*", "x"),
            audio_format=_lookup(AUDIO_FORMATS, get(1)),
            color_depth=_lookup(COLOR_DEPTHS, get(2)),
            color_format=_lookup(COLOR_FORMATS, get(3)),
            hdr_format=_lookup(HDR_FORMATS, get(4)),
            color_primaries=_lookup(COLOR_PRIMARIES, get(5)),
        )


def parse_int_list(raw: str | None) -> list[int]:
    """Parse a space separated list of integers, skipping junk."""
    if not raw:
        return []
    out: list[int] = []
    for item in raw.replace(",", " ").split():
        try:
            out.append(int(item))
        except ValueError:
            continue
    return out


def auth_digest(random: str, password: str) -> str:
    """PJLink class 1 authentication digest: md5(random + password)."""
    return hashlib.md5((random + password).encode()).hexdigest()  # noqa: S324


def build_frame(cls: int, cmd: str, param: str) -> str:
    """Build a command frame (without terminator)."""
    if len(param) > MAX_PARAM_LEN:
        raise ValueError("Parameter too long")
    if "\r" in param or "\n" in param:
        raise ValueError("Parameter must not contain line breaks")
    return f"%{cls}{cmd.strip().upper()} {param}"


class AetherionClient:
    """TCP control client. Keeps one session open and serialises commands."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        password: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Init."""
        self.host = host
        self.port = port
        self.password = password or None
        self.timeout = timeout
        # Shorter connect timeout while the projector is believed to be off,
        # so polls of a sleeping projector are cheap.
        self.connect_timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._auth_prefix = ""
        self._last_io = 0.0
        self._sent = False
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        """Return True if a session is open."""
        return self._writer is not None and not self._writer.is_closing()

    async def _connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.connect_timeout
            )
            greeting = await self._readline()
        except asyncio.CancelledError:
            # Never leave a half-open session (no greeting/auth) to be reused.
            await self._close()
            raise
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as err:
            await self._close()
            raise AetherionConnectionError(
                f"Cannot connect to {self.host}:{self.port}: {err!r}"
            ) from err

        _LOGGER.debug("Greeting from %s: %s", self.host, greeting)
        parts = greeting.split()
        if len(parts) < 2 or parts[0].upper() != "PJLINK":
            await self._close()
            raise AetherionConnectionError(f"Unexpected greeting: {greeting!r}")
        if parts[1].upper() == "ERRA":
            await self._close()
            raise AetherionAuthError("Projector rejected the session")
        if parts[1] == "1":
            if len(parts) < 3:
                await self._close()
                raise AetherionConnectionError(f"Malformed greeting: {greeting!r}")
            if not self.password:
                await self._close()
                raise AetherionAuthError("Projector requires a password")
            self._auth_prefix = auth_digest(parts[2], self.password)
        else:
            self._auth_prefix = ""
        self._last_io = time.monotonic()

    async def _readline(self) -> str:
        assert self._reader is not None
        data = await asyncio.wait_for(self._reader.readuntil(b"\r"), self.timeout)
        return data.decode("utf-8", errors="replace").strip("\r\n\x00 ")

    async def _close(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (OSError, TimeoutError):
                pass

    async def close(self) -> None:
        """Close the session."""
        async with self._lock:
            await self._close()

    async def _exchange(self, frame: str) -> str:
        """Send one frame and return the raw response data after ``=``."""
        assert self._writer is not None
        line = self._auth_prefix + frame
        self._auth_prefix = ""  # digest is only sent with the first command
        _LOGGER.debug("-> %s: %s", self.host, frame)
        self._writer.write(line.encode() + b"\r")
        self._sent = True
        await self._writer.drain()
        header = frame.split(" ", 1)[0]
        # Skip anything unrelated (should not happen on TCP, but be tolerant).
        for _ in range(5):
            resp = await self._readline()
            _LOGGER.debug("<- %s: %s", self.host, resp)
            self._last_io = time.monotonic()
            if resp.upper().startswith("PJLINK ERRA"):
                raise AetherionAuthError("Wrong password")
            key, sep, data = resp.partition("=")
            if sep and key.upper() == header.upper():
                return data
        raise AetherionConnectionError(f"No matching response for {frame!r}")

    async def _request(self, frame: str) -> str:
        """Send a frame with one reconnect-and-retry. Raises on ERRx."""
        async with self._lock:
            for attempt in (1, 2):
                if self.connected and time.monotonic() - self._last_io > IDLE_RECONNECT:
                    await self._close()
                self._sent = False
                try:
                    if not self.connected:
                        await self._connect()
                    data = await self._exchange(frame)
                    break
                except AetherionAuthError:
                    await self._close()
                    raise
                except asyncio.CancelledError:
                    # A reply may still arrive; drop the session so it can't be
                    # read as the answer to the next command.
                    await self._close()
                    raise
                except (
                    OSError,
                    TimeoutError,
                    asyncio.IncompleteReadError,
                    asyncio.LimitOverrunError,
                    AetherionConnectionError,
                ) as err:
                    await self._close()
                    # Only queries are safe to repeat once the frame went out;
                    # resending e.g. a volume step or key press would run it twice.
                    # While the projector is believed off (short connect
                    # timeout) don't double the cost of a failed connect.
                    if (
                        attempt == 2
                        or (self._sent and not frame.endswith(" ?"))
                        or (not self._sent and self.connect_timeout < self.timeout)
                    ):
                        raise AetherionConnectionError(
                            f"{frame!r} to {self.host} failed: {err!r}"
                        ) from err
                    _LOGGER.debug("Retrying %s after %r", frame, err)
        if data.strip().upper() in ERROR_TEXT:
            raise AetherionCommandError(frame, data.strip().upper())
        return data.strip()

    async def send(self, cls: int, cmd: str, param: str) -> str:
        """Send ``%<cls><cmd> <param>`` and return the response data."""
        return await self._request(build_frame(cls, cmd, param))

    async def query(self, cls: int, cmd: str) -> str:
        """Run a ``?`` query."""
        return await self.send(cls, cmd, "?")

    async def try_query(self, cls: int, cmd: str) -> str | None:
        """Query, returning None when the projector answers ERRx."""
        try:
            return await self.query(cls, cmd)
        except AetherionCommandError as err:
            _LOGGER.debug("%s", err)
            return None

    async def set(self, cls: int, cmd: str, param: str | int) -> None:
        """Run a set command expecting ``OK``."""
        await self.send(cls, cmd, str(param))

    # --- convenience -----------------------------------------------------------

    async def power_on(self) -> None:
        """Power on."""
        await self.set(1, "POWR", 1)

    async def power_off(self) -> None:
        """Enter standby."""
        await self.set(1, "POWR", 0)

    async def select_input(self, code: int) -> None:
        """Switch input."""
        await self.set(1, "INPT", code)

    async def set_mute(self, mute: bool) -> None:
        """Mute or unmute."""
        await self.set(1, "AVMT", 21 if mute else 20)

    async def set_volume(self, volume: int) -> None:
        """Set volume 0-100."""
        await self.set(3, "VOLM", max(0, min(100, int(volume))))

    async def volume_step(self, up: bool) -> None:
        """Step volume."""
        await self.set(2, "SVOL", 1 if up else 0)

    async def remote_key(self, key: str) -> None:
        """Send a navigation key."""
        key = key.strip().lower()
        key = REMOTE_KEY_ALIASES.get(key, key)
        if key not in REMOTE_KEYS:
            raise ValueError(f"Unknown key {key!r}; valid: {', '.join(REMOTE_KEYS)}")
        await self.set(3, "RCNC", REMOTE_KEYS[key])

    async def manual_focus(self) -> None:
        """Open the manual focus screen (``%3FCMN`` takes no parameter)."""
        await self._request("%3FCMN")

    async def send_raw(self, raw: str) -> str:
        """Send a raw frame such as ``%3LASL 8`` or ``%3FCMN``; returns data."""
        raw = raw.strip()
        if not raw.startswith("%") or len(raw) < 6 or not raw[1].isdigit():
            raise ValueError("Raw commands look like %1POWR 1 or %3PICT ?")
        cls, cmd, param = int(raw[1]), raw[2:6], raw[6:].strip()
        if not param:
            return await self._request(f"%{cls}{cmd.upper()}")
        return await self.send(cls, cmd, param)


def normalize_mac(mac: str) -> str:
    """Return ``aa:bb:cc:dd:ee:ff`` or raise ValueError."""
    hexmac = "".join(ch for ch in mac if ch.isalnum()).lower()
    if len(hexmac) != 12 or any(ch not in "0123456789abcdef" for ch in hexmac):
        raise ValueError(f"Invalid MAC address: {mac!r}")
    return ":".join(hexmac[i : i + 2] for i in range(0, 12, 2))


def send_magic_packet(mac: str, host: str | None = None) -> None:
    """Send Wake-on-LAN (blocking; run in an executor).

    Sent to the limited broadcast and, if known, unicast to the projector's
    last IP (the switch/AP may still have its MAC cached).
    """
    payload = bytes.fromhex("ff" * 6 + normalize_mac(mac).replace(":", "") * 16)
    targets = ["255.255.255.255"] + ([host] if host else [])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in targets:
            for port in (9, 7):
                try:
                    sock.sendto(payload, (target, port))
                except OSError as err:
                    _LOGGER.debug("Magic packet to %s:%s failed: %s", target, port, err)


# --- UDP search + status notifications ----------------------------------------------


@dataclass(slots=True)
class DiscoveredProjector:
    """A projector that answered %2SRCH."""

    host: str
    model: str
    name: str


def parse_ackn(data: str) -> tuple[str, str]:
    """Split the %2ACKN payload into (model, device name).

    Firmware Q1015 sends ``Aetherion MAX x Projector`` (" x " separator); the
    spec shows ``[Model Name] [Device Name]``, so fall back to a word split.
    """
    data = data.strip()
    if " x " in data:
        model, _, name = data.partition(" x ")
        return model.strip(), name.strip() or model.strip()
    words = data.split()
    if len(words) >= 2 and words[0].lower() == "aetherion":
        return " ".join(words[:2]), " ".join(words[2:]) or "Aetherion"
    if words:
        return words[0], " ".join(words[1:]) or words[0]
    return "Aetherion", "Aetherion"


def normalize_resolution(raw: str | None) -> str | None:
    """``3840 x 2160`` -> ``3840x2160``; ``0 x 0`` (no signal) -> None."""
    if not raw:
        return None
    value = raw.replace(" ", "").replace("*", "x").lower()
    width, _, height = value.partition("x")
    if not width.isdigit() or not height.isdigit() or int(width) == 0:
        return None
    return value


NotificationCallback = Callable[[str, str], None]


class _UDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, owner: NotificationListener) -> None:
        self._owner = owner

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        text = data.decode("utf-8", errors="replace")
        for line in text.replace("\n", "\r").split("\r"):
            line = line.strip("\x00 ")
            if line:
                self._owner.handle_line(addr[0], line)


class NotificationListener:
    """Listens on UDP 4352 for %2ACKN search replies and %2 status notifications."""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        """Init."""
        self.port = port
        self._transport: asyncio.DatagramTransport | None = None
        self._subscribers: dict[str, list[NotificationCallback]] = {}
        self._search_results: dict[str, DiscoveredProjector] | None = None

    @property
    def running(self) -> bool:
        """True if bound."""
        return self._transport is not None

    async def start(self) -> None:
        """Bind the socket. Raises OSError if the port is taken."""
        if self._transport:
            return
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        try:
            sock.bind(("0.0.0.0", self.port))  # noqa: S104
        except OSError:
            sock.close()
            raise
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UDPProtocol(self), sock=sock
        )
        self._transport = transport  # type: ignore[assignment]

    def stop(self) -> None:
        """Close the socket."""
        if self._transport:
            self._transport.close()
            self._transport = None

    def subscribe(self, host: str, callback: NotificationCallback) -> Callable[[], None]:
        """Call ``callback(command, data)`` for notifications from ``host``."""
        self._subscribers.setdefault(host, []).append(callback)

        def _remove() -> None:
            cbs = self._subscribers.get(host, [])
            if callback in cbs:
                cbs.remove(callback)

        return _remove

    def handle_line(self, host: str, line: str) -> None:
        """Dispatch a received line."""
        key, sep, data = line.partition("=")
        if not sep or not key.startswith("%") or len(key) < 6:
            return
        cmd = key[2:6].upper()
        _LOGGER.debug("UDP from %s: %s", host, line)
        if cmd == "ACKN":
            if self._search_results is not None:
                model, name = parse_ackn(data)
                self._search_results[host] = DiscoveredProjector(host, model, name)
            return
        for callback in list(self._subscribers.get(host, [])):
            try:
                callback(cmd, data.strip())
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Notification callback failed")

    def send_search(self, target: str = "255.255.255.255") -> None:
        """Send %2SRCH (also registers us as notification target)."""
        if self._transport:
            self._transport.sendto(b"%2SRCH\r", (target, self.port))

    async def search(self, timeout: float = 2.0) -> list[DiscoveredProjector]:
        """Broadcast %2SRCH and collect replies."""
        if not self._transport:
            return []
        self._search_results = {}
        try:
            self.send_search()
            await asyncio.sleep(timeout)
            return list(self._search_results.values())
        finally:
            self._search_results = None
