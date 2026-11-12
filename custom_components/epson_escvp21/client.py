"""Minimal asyncio ESC/VP21 client for a projector behind a serial-to-TCP bridge.

The bridge (e.g. a Waveshare serial server in TCP Server mode) forwards every
byte the projector sends to *every* connected TCP client. So any reply we read
may belong to a command another client sent. The client copes with that by:

* sending one command at a time (asyncio lock);
* discarding anything already buffered before each command;
* for queries, only accepting a line starting with the expected ``KEY=``;
* treating ``ERR`` as ours only if no matching reply arrives shortly after it.

This module has no Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

CR = b"\r"
READY = b":"
ERR = "ERR"

DEFAULT_QUERY_TIMEOUT = 3.0
DEFAULT_SET_TIMEOUT = 5.0
POWER_SET_TIMEOUT = 40.0
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_ERR_GRACE = 0.3
DEFAULT_DRAIN_WINDOW = 0.02
BACKOFF_MIN = 1.0
BACKOFF_MAX = 30.0

_KEY_RE = re.compile(r"^[A-Z0-9]+$")
_COMMAND_RE = re.compile(r"^[A-Za-z0-9 ?._-]+$")


class EscVp21Error(Exception):
    """Base error for the ESC/VP21 client."""


class ConnectionFailed(EscVp21Error):
    """The TCP connection could not be opened or was lost."""


class ResponseTimeout(EscVp21Error):
    """No matching reply arrived in time."""


class CommandRejected(EscVp21Error):
    """The projector answered ERR."""


@dataclass
class ClientStats:
    """Counters exposed through diagnostics."""

    connects: int = 0
    connection_failures: int = 0
    commands: int = 0
    timeouts: int = 0
    rejected: int = 0
    discarded_lines: int = 0
    drained_bytes: int = 0
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the stats as a plain dict."""
        return dict(self.__dict__)


@dataclass
class _Reader:
    """Splits the byte stream into ``:``-terminated frames of text lines."""

    buffer: bytearray = field(default_factory=bytearray)

    def feed(self, data: bytes) -> None:
        self.buffer.extend(data)

    def pop_frame(self) -> list[str] | None:
        """Return the lines of the next complete frame, if there is one."""
        idx = self.buffer.find(READY)
        if idx < 0:
            return None
        raw = bytes(self.buffer[:idx])
        del self.buffer[: idx + 1]
        text = raw.replace(b"\x00", b"").decode("ascii", errors="replace")
        return [line.strip() for line in text.split("\r") if line.strip()]

    def clear(self) -> int:
        size = len(self.buffer)
        self.buffer.clear()
        return size


class EscVp21Client:
    """One persistent, serialized ESC/VP21 connection."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        query_timeout: float = DEFAULT_QUERY_TIMEOUT,
        set_timeout: float = DEFAULT_SET_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        err_grace: float = DEFAULT_ERR_GRACE,
        drain_window: float = DEFAULT_DRAIN_WINDOW,
        backoff_min: float = BACKOFF_MIN,
        backoff_max: float = BACKOFF_MAX,
    ) -> None:
        self.host = host
        self.port = port
        self.query_timeout = query_timeout
        self.set_timeout = set_timeout
        self.connect_timeout = connect_timeout
        self.err_grace = err_grace
        self.drain_window = drain_window
        self.backoff_min = backoff_min
        self.backoff_max = backoff_max

        self.stats = ClientStats()
        self._lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._frames = _Reader()
        self._backoff = 0.0
        self._next_attempt = 0.0
        self._closed = False

    # -- connection ------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Return True if the socket is open."""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Open the connection now, ignoring any backoff."""
        async with self._lock:
            self._next_attempt = 0.0
            await self._ensure_connected()

    async def close(self) -> None:
        """Close the connection; later commands will fail."""
        self._closed = True
        await self._disconnect()

    async def _disconnect(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        self._frames.clear()
        if writer is None:
            return
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1)
        except (OSError, TimeoutError):
            pass

    async def _ensure_connected(self) -> None:
        if self._closed:
            raise ConnectionFailed("client is closed")
        if self.connected:
            return
        loop = asyncio.get_running_loop()
        if loop.time() < self._next_attempt:
            raise ConnectionFailed(
                f"reconnect to {self.host}:{self.port} backing off "
                f"for {self._next_attempt - loop.time():.1f}s"
            )
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.connect_timeout
            )
        except (OSError, TimeoutError) as err:
            self.stats.connection_failures += 1
            self._backoff = min(
                max(self._backoff * 2, self.backoff_min), self.backoff_max
            )
            self._next_attempt = loop.time() + self._backoff
            msg = f"cannot connect to {self.host}:{self.port}: {err!r}"
            self.stats.last_error = msg
            raise ConnectionFailed(msg) from err
        self._backoff = 0.0
        self._next_attempt = 0.0
        self._frames.clear()
        self.stats.connects += 1
        _LOGGER.debug("Connected to %s:%s", self.host, self.port)

    async def _lost(self, reason: str) -> ConnectionFailed:
        _LOGGER.debug("Connection to %s:%s lost: %s", self.host, self.port, reason)
        await self._disconnect()
        self.stats.last_error = f"connection lost: {reason}"
        return ConnectionFailed(reason)

    # -- low level I/O ---------------------------------------------------

    async def _drain(self) -> None:
        """Discard everything already received (replies to other clients)."""
        assert self._reader is not None
        dropped = self._frames.clear()
        while True:
            try:
                data = await asyncio.wait_for(
                    self._reader.read(4096), self.drain_window
                )
            except TimeoutError:
                break
            except OSError as err:
                raise await self._lost(repr(err)) from err
            if not data:
                raise await self._lost("EOF")
            dropped += len(data)
        if dropped:
            self.stats.drained_bytes += dropped
            _LOGGER.debug("Discarded %d stale bytes before command", dropped)

    async def _write(self, command: str) -> None:
        assert self._writer is not None
        _LOGGER.debug("-> %r", command)
        try:
            self._writer.write(command.encode("ascii") + CR)
            await self._writer.drain()
        except OSError as err:
            raise await self._lost(repr(err)) from err

    async def _read_frame(self, deadline: float) -> list[str]:
        """Return the next frame's lines, or raise TimeoutError at the deadline."""
        assert self._reader is not None
        loop = asyncio.get_running_loop()
        while (frame := self._frames.pop_frame()) is None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            try:
                data = await asyncio.wait_for(self._reader.read(256), remaining)
            except TimeoutError:  # subclass of OSError; not a dead socket
                raise
            except OSError as err:
                raise await self._lost(repr(err)) from err
            if not data:
                raise await self._lost("EOF")
            self._frames.feed(data)
        _LOGGER.debug("<- %r", frame)
        return frame

    async def _prepare(self) -> None:
        """Connect if needed and drain; reconnect once if the socket was dead."""
        await self._ensure_connected()
        try:
            await self._drain()
        except ConnectionFailed:
            await self._ensure_connected()
            await self._drain()

    # -- commands --------------------------------------------------------

    async def query(self, key: str, timeout: float | None = None) -> str:
        """Send ``KEY?`` and return the value from ``KEY=VALUE``.

        Raises CommandRejected on ERR, ResponseTimeout if no reply, and
        ConnectionFailed if the bridge is unreachable.
        """
        key = key.strip().upper()
        if not _KEY_RE.match(key):
            raise ValueError(f"invalid ESC/VP21 key: {key!r}")
        async with self._lock:
            try:
                return await self._query_locked(key, timeout)
            except ConnectionFailed:
                if self._closed:
                    raise
                # The socket died mid-command: one retry on a fresh connection.
                return await self._query_locked(key, timeout)

    async def _query_locked(self, key: str, timeout: float | None) -> str:
        await self._prepare()
        self.stats.commands += 1
        await self._write(f"{key}?")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (timeout or self.query_timeout)
        wait_until = deadline
        saw_err = False
        prefix = f"{key}="
        while True:
            try:
                lines = await self._read_frame(wait_until)
            except TimeoutError:
                if saw_err:
                    self.stats.rejected += 1
                    raise CommandRejected(f"{key}? -> ERR") from None
                self.stats.timeouts += 1
                self.stats.last_error = f"timeout waiting for {prefix}"
                raise ResponseTimeout(f"no {prefix} reply") from None
            for line in lines:
                if line.startswith(prefix):
                    return line[len(prefix) :].strip()
                if line == ERR:
                    # Might be ours, might be another client's. Wait briefly
                    # for a real answer before believing it.
                    if not saw_err:
                        saw_err = True
                        wait_until = min(deadline, loop.time() + self.err_grace)
                    continue
                self.stats.discarded_lines += 1
                _LOGGER.debug("Ignoring %r while waiting for %s", line, prefix)

    async def set(self, key: str, value: str, timeout: float | None = None) -> None:
        """Send ``KEY VALUE`` and wait for ``:``.

        Raises CommandRejected if only ERR comes back.
        """
        key = key.strip().upper()
        value = value.strip().upper()
        if not _KEY_RE.match(key) or not _KEY_RE.match(value):
            raise ValueError(f"invalid ESC/VP21 command: {key!r} {value!r}")
        if timeout is None:
            timeout = POWER_SET_TIMEOUT if key == "PWR" else self.set_timeout
        async with self._lock:
            await self._set_locked(f"{key} {value}", timeout)

    async def _set_locked(self, command: str, timeout: float) -> None:
        await self._prepare()
        self.stats.commands += 1
        await self._write(command)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        wait_until = deadline
        saw_err = False
        while True:
            try:
                lines = await self._read_frame(wait_until)
            except TimeoutError:
                if saw_err:
                    self.stats.rejected += 1
                    raise CommandRejected(f"{command} -> ERR") from None
                self.stats.timeouts += 1
                self.stats.last_error = f"timeout waiting for ack of {command}"
                raise ResponseTimeout(f"no ack for {command}") from None
            if not lines:
                return  # bare ':'
            if lines == [ERR]:
                if not saw_err:
                    saw_err = True
                    wait_until = min(deadline, loop.time() + self.err_grace)
                continue
            # KEY=VALUE replies belong to someone else's query.
            self.stats.discarded_lines += len(lines)

    async def send_raw(self, command: str, timeout: float | None = None) -> str:
        """Send any ESC/VP21 command and return the reply text.

        ``PWR?`` -> ``"PWR=01"``; ``SOURCE 30`` -> ``":"``; rejected -> ``"ERR"``.
        """
        command = command.strip()
        if not command or not _COMMAND_RE.match(command):
            raise ValueError(f"invalid ESC/VP21 command: {command!r}")
        try:
            if command.endswith("?"):
                key = command[:-1].strip().upper()
                return f"{key}={await self.query(key, timeout)}"
            key, _, value = command.partition(" ")
            if value:
                await self.set(key, value, timeout)
            else:
                # Bare keyword commands such as "KEY 3B" need a value; a single
                # word is sent verbatim and must still be acknowledged.
                key = key.upper()
                if not _KEY_RE.match(key):
                    raise ValueError(f"invalid ESC/VP21 command: {command!r}")
                async with self._lock:
                    await self._set_locked(key, timeout or self.set_timeout)
        except CommandRejected:
            return ERR
        return ":"
