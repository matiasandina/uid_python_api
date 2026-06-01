from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, List


MAGIC = b"TTLFRM01"
HEADER_STRUCT = struct.Struct("<8sHHIHHQ")
RECORD_STRUCT = struct.Struct("<IQQ")
HEADER_SIZE = HEADER_STRUCT.size
RECORD_SIZE = RECORD_STRUCT.size


@dataclass(frozen=True)
class TTLFrameIndexHeader:
    sampling_rate_hz: int
    frame_size: int
    record_count: int


@dataclass(frozen=True)
class TTLFrameIndexRecord:
    frame_id: int
    t_us_first_sample: int
    payload_offset_bytes: int


class TTLFrameIndexWriter:
    def __init__(self, path: str | Path, *, sampling_rate_hz: int, frame_size: int) -> None:
        self._path = Path(path)
        self._sampling_rate_hz = int(sampling_rate_hz)
        self._frame_size = int(frame_size)
        self._record_count = 0
        self._handle: BinaryIO | None = None

    def __enter__(self) -> "TTLFrameIndexWriter":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self._handle is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("w+b")
        self._handle.write(self._pack_header(record_count=0))
        self._handle.flush()

    def append(self, *, frame_id: int, t_us_first_sample: int, payload_offset_bytes: int) -> None:
        if self._handle is None:
            raise RuntimeError("TTLFrameIndexWriter is not open.")
        self._handle.write(
            RECORD_STRUCT.pack(
                int(frame_id),
                int(t_us_first_sample),
                int(payload_offset_bytes),
            )
        )
        self._record_count += 1

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.flush()
            self._handle.seek(0)
            self._handle.write(self._pack_header(record_count=self._record_count))
            self._handle.flush()
        finally:
            self._handle.close()
            self._handle = None

    def _pack_header(self, *, record_count: int) -> bytes:
        return HEADER_STRUCT.pack(
            MAGIC,
            HEADER_SIZE,
            RECORD_SIZE,
            self._sampling_rate_hz,
            self._frame_size,
            0,
            int(record_count),
        )


def read_frame_index(path: str | Path) -> tuple[TTLFrameIndexHeader, List[TTLFrameIndexRecord]]:
    payload = Path(path).read_bytes()
    if len(payload) < HEADER_SIZE:
        raise ValueError("TTL frame index file is truncated before the header.")

    magic, header_size, record_size, sampling_rate_hz, frame_size, _reserved, record_count = HEADER_STRUCT.unpack_from(
        payload, 0
    )
    if magic != MAGIC:
        raise ValueError(f"TTL frame index magic mismatch: expected {MAGIC!r}, got {magic!r}.")
    if header_size != HEADER_SIZE:
        raise ValueError(f"TTL frame index header size mismatch: expected {HEADER_SIZE}, got {header_size}.")
    if record_size != RECORD_SIZE:
        raise ValueError(f"TTL frame index record size mismatch: expected {RECORD_SIZE}, got {record_size}.")

    expected_size = HEADER_SIZE + int(record_count) * RECORD_SIZE
    if len(payload) != expected_size:
        raise ValueError(
            f"TTL frame index size mismatch: expected {expected_size} bytes for {record_count} record(s), "
            f"got {len(payload)} bytes."
        )

    records: List[TTLFrameIndexRecord] = []
    offset = HEADER_SIZE
    for _ in range(int(record_count)):
        frame_id, t_us_first_sample, payload_offset_bytes = RECORD_STRUCT.unpack_from(payload, offset)
        records.append(
            TTLFrameIndexRecord(
                frame_id=int(frame_id),
                t_us_first_sample=int(t_us_first_sample),
                payload_offset_bytes=int(payload_offset_bytes),
            )
        )
        offset += RECORD_SIZE

    return (
        TTLFrameIndexHeader(
            sampling_rate_hz=int(sampling_rate_hz),
            frame_size=int(frame_size),
            record_count=int(record_count),
        ),
        records,
    )
