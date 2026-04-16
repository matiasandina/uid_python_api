from __future__ import annotations

import time
from typing import Generator, Optional

import serial

from .protocol import TTLFrame, TTLFrameParser, TTLHandshake, parse_handshake_line, validate_handshake


class TeensyTTLReader:
    """Read handshake and framed TTL payloads from Teensy USB CDC."""

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout_seconds: float = 0.25,
        read_chunk_bytes: int = 4096,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._timeout_seconds = timeout_seconds
        self._read_chunk_bytes = read_chunk_bytes
        self._serial: Optional[serial.Serial] = None
        self._parser = TTLFrameParser()

    @property
    def serial_port(self) -> str:
        return self._port

    def open(self) -> None:
        if self._serial and self._serial.is_open:
            return
        self._serial = serial.Serial(
            port=self._port,
            baudrate=self._baudrate,
            timeout=self._timeout_seconds,
            write_timeout=self._timeout_seconds,
        )

    def close(self) -> None:
        if self._serial:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def read_handshake(self, timeout_seconds: float = 5.0) -> TTLHandshake:
        self._require_open()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            raw = self._serial.readline()
            if not raw:
                continue
            if raw.startswith(b"#"):
                handshake = parse_handshake_line(raw)
                validate_handshake(handshake)
                return handshake
        raise TimeoutError(f"Timed out waiting for TTL handshake from {self._port}.")

    def iter_frames(self) -> Generator[TTLFrame, None, None]:
        self._require_open()
        while True:
            chunk = self._serial.read(self._read_chunk_bytes)
            if not chunk:
                continue
            for frame in self._parser.feed(chunk):
                yield frame

    def _require_open(self) -> None:
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Serial port is not open.")
