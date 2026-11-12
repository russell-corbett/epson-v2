"""Fake ESC/VP21 projector behind a Waveshare-style serial-to-TCP bridge.

Like the real bridge, every byte the "projector" sends goes to *every*
connected TCP client, and commands from all clients share one serial line,
processed one at a time.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

READY = b":"

# Keys that answer even when the projector is not on.
# Matches the live 6030UB in standby: PWR, SNO and LAMP answer; ERR? does not.
ALWAYS_ANSWER = {"PWR", "SNO", "LAMP"}


@dataclass
class FakeProjector:
    """Simulated projector state and bridge."""

    state: dict[str, str] = field(
        default_factory=lambda: {
            "PWR": "01",
            "LAMP": "1275",
            "SOURCE": "30",
            "CMODE": "06",
            "LUMINANCE": "00",
            "ASPECT": "00",
            "ERR": "00",
            "SNO": "TZ2F380077L",
        }
    )
    reply_delay: float = 0.005
    # Valid values for set commands; anything else is ERR.
    valid: dict[str, set[str]] = field(
        default_factory=lambda: {
            "SOURCE": {"30", "A0"},
            "CMODE": {"06", "07", "0C", "15"},
            "LUMINANCE": {"00", "01"},
            "ASPECT": {"00", "30", "40"},
        }
    )

    def __post_init__(self) -> None:
        self.received: list[str] = []
        self.drop_next = 0  # swallow the next N commands (no reply at all)
        self.garbage_before_reply = b""  # bytes sent before each reply
        self._writers: list[asyncio.StreamWriter] = []
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._server: asyncio.Server | None = None
        self._worker: asyncio.Task | None = None
        self.port = 0

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        self._worker = asyncio.create_task(self._process())

    async def stop(self) -> None:
        await self.disconnect_all()
        if self._worker:
            self._worker.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def disconnect_all(self) -> None:
        """Drop every TCP client, as a bridge reboot would."""
        writers, self._writers = self._writers, []
        for writer in writers:
            writer.close()
        for writer in writers:
            try:
                await writer.wait_closed()
            except OSError:
                pass

    @property
    def client_count(self) -> int:
        return len(self._writers)

    # -- bridge ----------------------------------------------------------

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writers.append(writer)
        buf = b""
        try:
            while data := await reader.read(256):
                buf += data
                while b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    await self._queue.put(line.decode())
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            if writer in self._writers:
                self._writers.remove(writer)

    async def broadcast(self, data: bytes) -> None:
        """Send bytes to every connected client (bridge behaviour)."""
        for writer in list(self._writers):
            try:
                writer.write(data)
                await writer.drain()
            except (ConnectionError, RuntimeError):
                pass

    # -- projector -------------------------------------------------------

    async def _process(self) -> None:
        while True:
            command = await self._queue.get()
            self.received.append(command)
            if self.drop_next:
                self.drop_next -= 1
                continue
            await asyncio.sleep(self.reply_delay)
            reply = self.respond(command)
            if self.garbage_before_reply:
                await self.broadcast(self.garbage_before_reply)
            await self.broadcast(reply)

    def respond(self, command: str) -> bytes:
        command = command.strip()
        if command == "":
            return READY
        if command.endswith("?"):
            key = command[:-1]
            if key not in self.state:
                return b"ERR\r:"
            if self.state["PWR"] != "01" and key not in ALWAYS_ANSWER:
                return b"ERR\r:"
            return f"{key}={self.state[key]}\r:".encode()
        key, _, value = command.partition(" ")
        if key == "PWR":
            if self.state["PWR"] in ("02", "03"):
                return b"ERR\r:"
            if value == "ON":
                self.state["PWR"] = "01"
                return READY
            if value == "OFF":
                self.state["PWR"] = "00"
                return READY
            return b"ERR\r:"
        if self.state["PWR"] != "01":
            return b"ERR\r:"
        if key in self.valid and value in self.valid[key]:
            self.state[key] = value
            return READY
        return b"ERR\r:"


class OtherClient:
    """Another TCP client (e.g. a second Home Assistant) chattering on the bridge."""

    def __init__(self, port: int, commands: list[str], interval: float) -> None:
        self.port = port
        self.commands = commands
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def start(self) -> None:
        reader, self._writer = await asyncio.open_connection("127.0.0.1", self.port)
        self._reader_task = asyncio.create_task(self._sink(reader))
        self._task = asyncio.create_task(self._run())

    async def _sink(self, reader: asyncio.StreamReader) -> None:
        while await reader.read(1024):
            pass

    async def _run(self) -> None:
        assert self._writer
        i = 0
        while True:
            self._writer.write(self.commands[i % len(self.commands)].encode() + b"\r")
            await self._writer.drain()
            i += 1
            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        for task in (self._task, self._reader_task):
            if task:
                task.cancel()
        if self._writer:
            self._writer.close()
