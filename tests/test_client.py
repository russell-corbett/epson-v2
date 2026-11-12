"""Tests for the standalone ESC/VP21 client."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.epson_escvp21.client import (
    CommandRejected,
    ConnectionFailed,
    EscVp21Client,
    ResponseTimeout,
)

from .fake_projector import FakeProjector, OtherClient


def make_client(fake: FakeProjector, **kwargs) -> EscVp21Client:
    kwargs.setdefault("query_timeout", 0.5)
    kwargs.setdefault("set_timeout", 0.5)
    kwargs.setdefault("err_grace", 0.1)
    kwargs.setdefault("backoff_min", 0.2)
    kwargs.setdefault("backoff_max", 0.4)
    return EscVp21Client("127.0.0.1", fake.port, **kwargs)


async def test_query(projector: FakeProjector) -> None:
    client = make_client(projector)
    assert await client.query("PWR") == "01"
    assert await client.query("lamp") == "1275"
    assert await client.query("SNO") == "TZ2F380077L"
    assert projector.received == ["PWR?", "LAMP?", "SNO?"]
    await client.close()


async def test_unsupported_query_is_rejected(projector: FakeProjector) -> None:
    client = make_client(projector)
    with pytest.raises(CommandRejected):
        await client.query("VOLUME")
    # Still in sync afterwards.
    assert await client.query("CMODE") == "06"
    await client.close()


async def test_stays_in_sync_with_other_client(projector: FakeProjector) -> None:
    """Another client hammering the bridge must never confuse our replies."""
    other = OtherClient(
        projector.port,
        ["LAMP?", "SOURCE?", "VOLUME?", "CMODE?", "ASPECT?", "SOURCE 30", "ERR?"],
        interval=0.02,  # slower than the projector answers, like a real poller
    )
    await other.start()
    client = make_client(projector)
    expected = {
        "PWR": "01",
        "SOURCE": "30",
        "CMODE": "06",
        "LUMINANCE": "00",
        "ASPECT": "00",
        "ERR": "00",
        "LAMP": "1275",
    }
    try:
        for _ in range(15):
            for key, value in expected.items():
                assert await client.query(key) == value
    finally:
        await other.stop()
        await client.close()
    assert client.stats.discarded_lines > 0 or client.stats.drained_bytes > 0


async def test_foreign_err_does_not_reject_our_query(projector: FakeProjector) -> None:
    """An ERR meant for someone else, just before our reply, is ignored."""
    projector.garbage_before_reply = b"ERR\r:SOURCE=A0\r::"
    client = make_client(projector)
    assert await client.query("CMODE") == "06"
    assert await client.query("PWR") == "01"
    await client.close()


async def test_garbage_is_ignored(projector: FakeProjector) -> None:
    projector.garbage_before_reply = b"\x00\xffjunk\rLAMP=9\r:"
    client = make_client(projector)
    assert await client.query("PWR") == "01"
    await client.close()


async def test_timeout_then_recovers(projector: FakeProjector) -> None:
    client = make_client(projector)
    projector.drop_next = 1
    with pytest.raises(ResponseTimeout):
        await client.query("PWR")
    assert await client.query("PWR") == "01"
    assert client.stats.timeouts == 1
    await client.close()


async def test_late_reply_is_not_mistaken_for_next(projector: FakeProjector) -> None:
    """A reply that arrives after we gave up is discarded, not used later."""
    client = make_client(projector, query_timeout=0.1)
    projector.reply_delay = 0.2
    with pytest.raises(ResponseTimeout):
        await client.query("LAMP")
    projector.reply_delay = 0.005
    await asyncio.sleep(0.15)  # LAMP=1275 lands in our buffer now
    client.query_timeout = 0.5
    assert await client.query("CMODE") == "06"
    await client.close()


async def test_reconnects_after_drop(projector: FakeProjector) -> None:
    client = make_client(projector)
    assert await client.query("PWR") == "01"
    await projector.disconnect_all()
    await asyncio.sleep(0.05)
    assert await client.query("PWR") == "01"
    assert client.stats.connects == 2
    await client.close()


async def test_backoff_when_bridge_down(socket_enabled: None) -> None:
    fake = FakeProjector()
    await fake.start()
    port = fake.port
    await fake.stop()
    client = EscVp21Client("127.0.0.1", port, backoff_min=0.3, backoff_max=1)
    with pytest.raises(ConnectionFailed):
        await client.query("PWR")
    with pytest.raises(ConnectionFailed, match="backing off"):
        await client.query("PWR")
    assert client.stats.connection_failures == 1
    await client.close()


async def test_reconnects_after_bridge_returns(socket_enabled: None) -> None:
    fake = FakeProjector()
    await fake.start()
    port = fake.port
    await fake.stop()
    client = EscVp21Client("127.0.0.1", port, backoff_min=0.1, backoff_max=0.1)
    with pytest.raises(ConnectionFailed):
        await client.query("PWR")

    server = await asyncio.start_server(
        lambda r, w: None, "127.0.0.1", port  # placeholder to prove port reuse
    )
    server.close()
    await server.wait_closed()

    revived = FakeProjector()
    revived._server = await asyncio.start_server(revived._handle, "127.0.0.1", port)
    revived.port = port
    revived._worker = asyncio.create_task(revived._process())
    try:
        await asyncio.sleep(0.15)
        assert await client.query("PWR") == "01"
    finally:
        await client.close()
        await revived.stop()


async def test_source_command_format(projector: FakeProjector) -> None:
    client = make_client(projector)
    await client.set("SOURCE", "A0")
    assert projector.received[-1] == "SOURCE A0"
    assert projector.state["SOURCE"] == "A0"
    await client.set("source", "30")
    assert projector.received[-1] == "SOURCE 30"
    await client.close()


async def test_set_rejected(projector: FakeProjector) -> None:
    client = make_client(projector)
    with pytest.raises(CommandRejected):
        await client.set("CMODE", "99")
    await client.close()


async def test_set_ignores_foreign_query_replies(projector: FakeProjector) -> None:
    projector.garbage_before_reply = b"LAMP=1275\r:"
    client = make_client(projector)
    await client.set("CMODE", "07")
    assert projector.state["CMODE"] == "07"
    await client.close()


async def test_set_rejected_when_not_on(projector: FakeProjector) -> None:
    projector.state["PWR"] = "00"
    client = make_client(projector)
    with pytest.raises(CommandRejected):
        await client.set("SOURCE", "30")
    with pytest.raises(CommandRejected):
        await client.query("SOURCE")
    assert await client.query("PWR") == "00"
    await client.close()


async def test_send_raw(projector: FakeProjector) -> None:
    client = make_client(projector)
    assert await client.send_raw("PWR?") == "PWR=01"
    assert await client.send_raw("volume?") == "ERR"
    assert await client.send_raw("SOURCE A0") == ":"
    assert await client.send_raw("SOURCE ZZ") == "ERR"
    with pytest.raises(ValueError):
        await client.send_raw("PWR?\rPWR OFF")
    await client.close()


async def test_commands_are_serialized(projector: FakeProjector) -> None:
    client = make_client(projector)
    keys = ["PWR", "SOURCE", "CMODE", "LUMINANCE", "ASPECT", "LAMP"] * 3
    results = await asyncio.gather(*(client.query(k) for k in keys))
    assert results == [projector.state[k] for k in keys]
    await client.close()


async def test_closed_client_refuses(projector: FakeProjector) -> None:
    client = make_client(projector)
    await client.close()
    with pytest.raises(ConnectionFailed):
        await client.query("PWR")
