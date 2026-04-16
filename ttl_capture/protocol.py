from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import List

FRAME_MAGIC = b"\xAA\x55"
FRAME_HEADER_STRUCT = struct.Struct("<2sIHQ")
FRAME_HEADER_SIZE = FRAME_HEADER_STRUCT.size
HANDSHAKE_PREFIX = b"#TTL_HANDSHAKE "
HANDSHAKE_VERSION = 1
EXPECTED_SAMPLE_RATE_HZ = 20_000
EXPECTED_FRAME_SIZE = 2048
EXPECTED_CHANNEL_MAP = [1, 2, 3, 4]
MAX_FRAME_SAMPLES = 4096


class HandshakeError(ValueError):
    """Raised when handshake parsing or validation fails."""


@dataclass(frozen=True)
class TTLHandshake:
    version: int
    sampling_rate_hz: int
    frame_size: int
    channel_map: List[int]
    firmware_version: str
    git_hash: str


@dataclass(frozen=True)
class TTLFrame:
    frame_id: int
    n_samples: int
    t_us_first_sample: int
    payload: bytes


def parse_handshake_line(raw_line: bytes) -> TTLHandshake:
    line = raw_line.strip()
    if not line.startswith(HANDSHAKE_PREFIX):
        raise HandshakeError("Handshake prefix missing.")
    try:
        payload = json.loads(line[len(HANDSHAKE_PREFIX) :].decode("utf-8"))
    except Exception as exc:
        raise HandshakeError("Invalid handshake JSON.") from exc

    if not isinstance(payload, dict):
        raise HandshakeError("Handshake payload must be a JSON object.")

    try:
        handshake = TTLHandshake(
            version=int(payload.get("version", 0)),
            sampling_rate_hz=int(payload["sampling_rate_hz"]),
            frame_size=int(payload["frame_size"]),
            channel_map=[int(v) for v in payload["channel_map"]],
            firmware_version=str(payload.get("firmware_version", "")),
            git_hash=str(payload.get("git_hash", "")),
        )
    except Exception as exc:
        raise HandshakeError("Handshake payload missing required fields.") from exc

    return handshake


def validate_handshake(
    handshake: TTLHandshake,
    expected_sampling_rate_hz: int = EXPECTED_SAMPLE_RATE_HZ,
    expected_frame_size: int = EXPECTED_FRAME_SIZE,
    expected_channel_map: List[int] | None = None,
) -> None:
    expected_channel_map = expected_channel_map or list(EXPECTED_CHANNEL_MAP)
    if handshake.version != HANDSHAKE_VERSION:
        raise HandshakeError(
            f"Handshake version mismatch: expected {HANDSHAKE_VERSION}, got {handshake.version}."
        )
    if handshake.sampling_rate_hz != expected_sampling_rate_hz:
        raise HandshakeError(
            f"Sampling rate mismatch: expected {expected_sampling_rate_hz}, got {handshake.sampling_rate_hz}."
        )
    if handshake.frame_size != expected_frame_size:
        raise HandshakeError(
            f"Frame size mismatch: expected {expected_frame_size}, got {handshake.frame_size}."
        )
    if list(handshake.channel_map) != list(expected_channel_map):
        raise HandshakeError(
            f"Channel map mismatch: expected {expected_channel_map}, got {handshake.channel_map}."
        )


class TTLFrameParser:
    """Incremental frame parser with sync and re-sync support."""

    def __init__(self, max_frame_samples: int = MAX_FRAME_SAMPLES) -> None:
        self._buffer = bytearray()
        self._max_frame_samples = max_frame_samples

    def feed(self, data: bytes) -> List[TTLFrame]:
        if data:
            self._buffer.extend(data)

        frames: List[TTLFrame] = []
        while True:
            if len(self._buffer) < 2:
                break

            magic_pos = self._buffer.find(FRAME_MAGIC)
            if magic_pos < 0:
                last_byte = self._buffer[-1:]
                self._buffer.clear()
                if last_byte == FRAME_MAGIC[:1]:
                    self._buffer.extend(last_byte)
                break

            if magic_pos > 0:
                del self._buffer[:magic_pos]

            if len(self._buffer) < FRAME_HEADER_SIZE:
                break

            _, frame_id, n_samples, t_us_first_sample = FRAME_HEADER_STRUCT.unpack_from(self._buffer, 0)
            if n_samples == 0 or n_samples > self._max_frame_samples:
                # Probably desynced: move one byte forward and re-scan.
                del self._buffer[0]
                continue

            frame_size = FRAME_HEADER_SIZE + n_samples
            if len(self._buffer) < frame_size:
                break

            payload = bytes(self._buffer[FRAME_HEADER_SIZE:frame_size])
            frames.append(
                TTLFrame(
                    frame_id=frame_id,
                    n_samples=n_samples,
                    t_us_first_sample=t_us_first_sample,
                    payload=payload,
                )
            )
            del self._buffer[:frame_size]

        return frames
