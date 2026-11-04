"""In-process fake Aetherion projector speaking the TCP control protocol."""

from __future__ import annotations

import asyncio
import hashlib


class FakeProjector:
    """Minimal stateful emulation of the AWOL command set."""

    RANDOM = "56bb589c"

    def __init__(self, password: str | None = None, port: int = 0) -> None:
        self.password = password
        self.server: asyncio.base_events.Server | None = None
        self.port = port
        self.received: list[str] = []
        self.connections = 0
        self._writers: set[asyncio.StreamWriter] = set()
        self.respond_override: dict[str, str] = {}
        self.drop_connections = 0  # close this many connections before greeting
        self.silent = False  # accept but never send a greeting
        self.state = {
            "1POWR": "1",
            "1INPT": "31",
            "1INST": "30 31 32 33 34 35",
            "1AVMT": "20",
            "1NAME": "Aetherion",
            "1INF1": "AWOL VISION",
            "1INF2": "Aetherion Max",
            "1INFO": "Dark Gunmetal",
            "1CLSS": "2",
            "2SVER": "V0000.01.00E.Q0909 26",
            "2IRES": "3840x2160",
            "2RRES": "3840x2160",
            "3VOLM": "50",
            "3PICL": "Vivid,Standard,Sports,Energy Saving,PC/Game,Theater,FILMMAKER MODE",
            "3PICT": "Standard",
            "3LASL": "8",
            "3ENBL": "0",
            "3MOTN": "4",
            "3GAMA": "1",
            "3DYTM": "0",
            "3CLTP": "2",
            "3ASPR": "3",
            "3SINF": "3840x2160@60Hz 4 1 3 1 2",
            "3FANS": "1500 1500 1530 1170 1200 0 0",
            "3TEMP": "22 43 0 0 0 0 0 0",
            "3LEDS": "0",
        }
        # Commands that answer ERR3 while in standby
        self.standby_ok = {"1POWR", "1NAME", "1INF1", "1INF2", "1INFO", "1CLSS", "3LEDS"}

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            for writer in list(self._writers):
                writer.transport.abort()
            await self.server.wait_closed()
            self.server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.add(writer)
        if self.drop_connections:
            self.drop_connections -= 1
            self._writers.discard(writer)
            writer.close()
            return
        if self.silent:
            try:
                await reader.read()
            finally:
                self._writers.discard(writer)
                writer.close()
            return
        authed = self.password is None
        writer.write(b"PJLINK 0\r" if authed else f"PJLINK 1 {self.RANDOM}\r".encode())
        try:
            while True:
                raw = (await reader.readuntil(b"\r")).decode().strip("\r")
                if not authed:
                    digest = hashlib.md5((self.RANDOM + self.password).encode()).hexdigest()
                    if not raw.startswith(digest):
                        writer.write(b"PJLINK ERRA\r")
                        await writer.drain()
                        continue
                    raw = raw[len(digest):]
                    authed = True
                self.received.append(raw)
                writer.write((self.respond(raw) + "\r").encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def respond(self, frame: str) -> str:
        head, _, param = frame.partition(" ")
        key = head[1:].upper()
        if param != "?" and key in self.respond_override:
            return f"{head}={self.respond_override[key]}"
        if key not in self.state and key not in ("3RCNC", "2SVOL", "3FCMN"):
            return f"{head}=ERR1"
        on = self.state["1POWR"] == "1"
        if not on and key not in self.standby_ok:
            return f"{head}=ERR3"
        if param == "?":
            return f"{head}={self.state[key]}"
        if key == "3FCMN" or key == "3RCNC":
            return f"{head}=OK"
        if key == "2SVOL":
            vol = int(self.state["3VOLM"]) + (1 if param == "1" else -1)
            self.state["3VOLM"] = str(max(0, min(100, vol)))
            return f"{head}=OK"
        if key == "1POWR":
            if param not in ("0", "1"):
                return f"{head}=ERR2"
        if key == "1INPT" and param not in self.state["1INST"].split():
            return f"{head}=ERR2"
        if key == "3PICT" and param not in self.state["3PICL"].split(","):
            return f"{head}=ERR2"
        if key == "3VOLM" and not (param.isdigit() and 0 <= int(param) <= 100):
            return f"{head}=ERR2"
        self.state[key] = param
        return f"{head}=OK"
