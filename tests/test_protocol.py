"""Protocol client tests (no Home Assistant involved)."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.aetherion.protocol import (
    AetherionAuthError,
    AetherionClient,
    AetherionCommandError,
    AetherionConnectionError,
    NotificationListener,
    SignalInfo,
    auth_digest,
    build_frame,
    normalize_mac,
    normalize_resolution,
    parse_ackn,
    parse_int_list,
)

from .fake_projector import FakeProjector


def test_auth_digest_matches_pjlink() -> None:
    # PJLink spec example: random 498e4a67 + JBMIAProjectorLink
    assert auth_digest("498e4a67", "JBMIAProjectorLink") == "5d8409bc1c3fa39749434aa3a5c38682"


def test_build_frame() -> None:
    assert build_frame(1, "INST ", "?") == "%1INST ?"
    with pytest.raises(ValueError):
        build_frame(3, "PICT", "a\rb")


def test_signal_info_parse() -> None:
    info = SignalInfo.parse("3840x2160@60Hz 4 1 3 1 2")
    assert info.timing == "3840x2160@60Hz"
    assert info.audio_format == "Dolby Atmos"
    assert info.color_depth == "8-bit"
    assert info.color_format == "RGB"
    assert info.hdr_format == "HDR10"
    assert info.color_primaries == "BT.2020"
    # Real no-signal reply from firmware Q1015 on the Home input
    empty = SignalInfo.parse("0*0p@0Hz 0 255 3 0 0")
    assert empty == SignalInfo()


def test_parsers() -> None:
    assert parse_int_list("30 31 x 35") == [30, 31, 35]
    assert parse_int_list(None) == []
    assert parse_ackn("Aetherion Max Living Room") == ("Aetherion Max", "Living Room")
    assert parse_ackn("Aetherion MAX x Projector") == ("Aetherion MAX", "Projector")
    assert normalize_resolution("3840 x 2160") == "3840x2160"
    assert normalize_resolution("0 x 0") is None


async def test_queries_and_sets(projector: FakeProjector) -> None:
    client = AetherionClient("127.0.0.1", projector.port)
    assert await client.query(1, "POWR") == "1"
    assert await client.query(1, "INF2") == "Aetherion Max"
    await client.select_input(33)
    assert await client.query(1, "INPT") == "33"
    await client.set_volume(150)
    assert projector.state["3VOLM"] == "100"
    await client.remote_key("Select")
    assert projector.received[-1] == "%3RCNC 4"
    await client.manual_focus()
    assert projector.received[-1] == "%3FCMN"
    assert await client.send_raw("%3LASL 5") == "OK"
    assert projector.state["3LASL"] == "5"
    assert projector.connections == 1  # one persistent session
    await client.close()


async def test_errors(projector: FakeProjector) -> None:
    client = AetherionClient("127.0.0.1", projector.port)
    with pytest.raises(AetherionCommandError) as err:
        await client.set(1, "INPT", 99)
    assert err.value.code == "ERR2"
    projector.state["1POWR"] = "0"
    assert await client.try_query(3, "PICT") is None
    with pytest.raises(ValueError):
        await client.remote_key("jump")
    await client.close()


async def test_reconnects_after_drop(projector: FakeProjector) -> None:
    client = AetherionClient("127.0.0.1", projector.port)
    await client.query(1, "POWR")
    # Simulate the projector closing the idle session.
    assert client._writer is not None
    client._writer.transport.abort()
    await asyncio.sleep(0)
    assert await client.query(1, "POWR") == "1"
    assert projector.connections == 2
    await client.close()


async def test_set_not_resent_after_drop(projector: FakeProjector) -> None:
    """A key press must not run twice if the reply is lost."""
    client = AetherionClient("127.0.0.1", projector.port, timeout=0.5)
    await client.query(1, "POWR")
    original = projector.respond

    def swallow(frame: str) -> str:
        projector.respond = original
        raise ConnectionResetError  # projector processed it, reply never sent

    projector.respond = swallow
    with pytest.raises(AetherionConnectionError):
        await client.remote_key("up")
    assert projector.received.count("%3RCNC 0") == 1
    await client.close()


async def test_auth(socket_enabled: None) -> None:
    fake = FakeProjector(password="AWOLLink")
    await fake.start()
    try:
        client = AetherionClient("127.0.0.1", fake.port, "AWOLLink")
        assert await client.query(1, "POWR") == "1"
        await client.close()

        with pytest.raises(AetherionAuthError):
            await AetherionClient("127.0.0.1", fake.port).query(1, "POWR")
        bad = AetherionClient("127.0.0.1", fake.port, "nope")
        with pytest.raises(AetherionAuthError):
            await bad.query(1, "POWR")
        await bad.close()
    finally:
        await fake.stop()


async def test_connection_refused(socket_enabled: None) -> None:
    client = AetherionClient("127.0.0.1", 1, timeout=1)
    with pytest.raises(AetherionConnectionError):
        await client.query(1, "POWR")


def test_listener_dispatch() -> None:
    listener = NotificationListener()
    seen: list[tuple[str, str]] = []
    unsub = listener.subscribe("10.0.0.5", lambda c, d: seen.append((c, d)))
    listener.handle_line("10.0.0.5", "%2POWR=1")
    listener.handle_line("10.0.0.6", "%2POWR=0")
    listener.handle_line("10.0.0.5", "garbage")
    assert seen == [("POWR", "1")]
    unsub()
    listener.handle_line("10.0.0.5", "%2INPT=31")
    assert seen == [("POWR", "1")]
    listener._search_results = {}
    listener.handle_line("10.0.0.7", "%2ACKN=Aetherion Pro Bedroom")
    assert listener._search_results["10.0.0.7"].model == "Aetherion Pro"


def test_mac_helpers() -> None:
    assert normalize_mac("AA-BB-CC-00-11-22") == "aa:bb:cc:00:11:22"
    with pytest.raises(ValueError):
        normalize_mac("nope")


async def test_connect_failure_retried_when_on(projector: FakeProjector) -> None:
    """A transient connect/greeting failure is retried (projector believed on)."""
    projector.drop_connections = 1
    client = AetherionClient("127.0.0.1", projector.port)
    assert await client.query(1, "POWR") == "1"
    assert projector.connections == 2
    await client.close()


async def test_connect_failure_not_retried_when_off(projector: FakeProjector) -> None:
    projector.drop_connections = 1
    client = AetherionClient("127.0.0.1", projector.port, timeout=2)
    client.connect_timeout = 0.5  # coordinator sets this while off
    with pytest.raises(AetherionConnectionError):
        await client.query(1, "POWR")
    assert projector.connections == 1


async def test_cancel_during_greeting_closes_session(projector: FakeProjector) -> None:
    projector.silent = True
    client = AetherionClient("127.0.0.1", projector.port, timeout=5)
    task = asyncio.create_task(client.query(1, "POWR"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client.connected
