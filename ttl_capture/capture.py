from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from project_metadata import get_build_info
from .frame_index import TTLFrameIndexWriter
from .protocol import EXPECTED_SAMPLE_RATE_HZ, TTLHandshake
from .reader import TeensyTTLReader


@dataclass
class TTLStatus:
    running: bool
    serial_port: str
    frames_received: int
    dropped_frames: int
    bytes_written: int
    last_frame_id: Optional[int]
    last_pulse_monotonic_ns: List[Optional[int]]
    edge_rate_hz: List[float]
    errors: int
    last_error: Optional[str]


class TTLCaptureService:
    """Background reader/writer for Teensy TTL payload frames."""

    def __init__(
        self,
        session_folder: str,
        port: str,
        baudrate: int = 115200,
        timeout_seconds: float = 0.25,
        read_chunk_bytes: int = 4096,
        expected_sample_rate_hz: int = EXPECTED_SAMPLE_RATE_HZ,
    ) -> None:
        self._session_folder = Path(session_folder)
        self._port = port
        self._baudrate = baudrate
        self._timeout_seconds = timeout_seconds
        self._read_chunk_bytes = read_chunk_bytes
        self._expected_sample_rate_hz = expected_sample_rate_hz

        self._reader = TeensyTTLReader(
            port=port,
            baudrate=baudrate,
            timeout_seconds=timeout_seconds,
            read_chunk_bytes=read_chunk_bytes,
        )

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._status_lock = threading.Lock()

        self._running = False
        self._frames_received = 0
        self._dropped_frames = 0
        self._bytes_written = 0
        self._last_frame_id: Optional[int] = None
        self._last_pulse_monotonic_ns: List[Optional[int]] = [None, None, None, None]
        self._edge_rate_hz: List[float] = [0.0, 0.0, 0.0, 0.0]
        self._edge_counter: List[int] = [0, 0, 0, 0]
        self._edge_window_t0_ns = time.monotonic_ns()
        self._prev_sample_mask = 0
        self._errors = 0
        self._last_error: Optional[str] = None

        self._t0_monotonic_ns: Optional[int] = None
        self._t0_frame_id: Optional[int] = None
        self._handshake: Optional[TTLHandshake] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="TTLCaptureService")
        self._thread.start()

    def stop(self, join_timeout_seconds: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=join_timeout_seconds)

    def status(self) -> TTLStatus:
        with self._status_lock:
            return TTLStatus(
                running=self._running,
                serial_port=self._port,
                frames_received=self._frames_received,
                dropped_frames=self._dropped_frames,
                bytes_written=self._bytes_written,
                last_frame_id=self._last_frame_id,
                last_pulse_monotonic_ns=list(self._last_pulse_monotonic_ns),
                edge_rate_hz=list(self._edge_rate_hz),
                errors=self._errors,
                last_error=self._last_error,
            )

    def status_dict(self) -> Dict[str, Any]:
        snapshot = self.status()
        return {
            "running": snapshot.running,
            "serial_port": snapshot.serial_port,
            "frames_received": snapshot.frames_received,
            "dropped_frames": snapshot.dropped_frames,
            "bytes_written": snapshot.bytes_written,
            "last_frame_id": snapshot.last_frame_id,
            "last_pulse_monotonic_ns": snapshot.last_pulse_monotonic_ns,
            "edge_rate_hz": snapshot.edge_rate_hz,
            "errors": snapshot.errors,
            "last_error": snapshot.last_error,
        }

    def _run(self) -> None:
        raw_path = self._session_folder / "ttl_raw.bin"
        frames_path = self._session_folder / "ttl_frames.bin"
        meta_path = self._session_folder / "ttl_meta.json"
        self._session_folder.mkdir(parents=True, exist_ok=True)

        with self._status_lock:
            self._running = True
            self._last_error = None

        try:
            self._reader.open()
            self._handshake = self._reader.read_handshake()
            self._write_meta(meta_path, self._handshake)

            with (
                raw_path.open("ab") as raw_file,
                TTLFrameIndexWriter(
                    frames_path,
                    sampling_rate_hz=self._handshake.sampling_rate_hz,
                    frame_size=self._handshake.frame_size,
                ) as frame_index,
            ):
                expected_next_frame_id: Optional[int] = None
                for frame in self._reader.iter_frames():
                    if self._stop_event.is_set():
                        break

                    if expected_next_frame_id is not None and frame.frame_id != expected_next_frame_id:
                        gap = frame.frame_id - expected_next_frame_id
                        if gap > 0:
                            with self._status_lock:
                                self._dropped_frames += gap
                    expected_next_frame_id = frame.frame_id + 1

                    if self._t0_monotonic_ns is None:
                        self._t0_monotonic_ns = time.monotonic_ns()
                        self._t0_frame_id = frame.frame_id
                        self._write_meta(meta_path, self._handshake)

                    payload_offset_bytes = raw_file.tell()
                    raw_file.write(frame.payload)
                    raw_file.flush()
                    frame_index.append(
                        frame_id=frame.frame_id,
                        t_us_first_sample=frame.t_us_first_sample,
                        payload_offset_bytes=payload_offset_bytes,
                    )

                    with self._status_lock:
                        self._frames_received += 1
                        self._bytes_written += len(frame.payload)
                        self._last_frame_id = frame.frame_id

                    self._update_edge_status(frame.payload)
        except Exception as exc:
            with self._status_lock:
                self._errors += 1
                self._last_error = str(exc)
        finally:
            self._reader.close()
            with self._status_lock:
                self._running = False

    def _write_meta(self, path: Path, handshake: TTLHandshake) -> None:
        build = get_build_info()
        payload = {
            "sampling_rate_hz": handshake.sampling_rate_hz,
            "frame_size": handshake.frame_size,
            "channel_map": handshake.channel_map,
            "firmware_version": handshake.firmware_version,
            "firmware_git_hash": handshake.git_hash,
            "t0_monotonic_ns": self._t0_monotonic_ns,
            "t0_frame_id": self._t0_frame_id,
            "wall_clock_start_iso": datetime.now(timezone.utc).isoformat(),
            "python_version": build["version"],
            "python_git_hash": build["commit"],
            "python_git_dirty": build["dirty"],
            "serial_port": self._port,
            "baudrate": self._baudrate,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _update_edge_status(self, payload: bytes) -> None:
        now_ns = time.monotonic_ns()
        prev_mask = self._prev_sample_mask

        for sample in payload:
            rising = (~prev_mask) & sample & 0x0F
            if rising:
                for ch in range(4):
                    if (rising >> ch) & 0x01:
                        self._last_pulse_monotonic_ns[ch] = now_ns
                        self._edge_counter[ch] += 1
            prev_mask = sample

        self._prev_sample_mask = prev_mask

        elapsed_ns = now_ns - self._edge_window_t0_ns
        if elapsed_ns >= 1_000_000_000:
            elapsed_s = elapsed_ns / 1e9
            with self._status_lock:
                for ch in range(4):
                    self._edge_rate_hz[ch] = self._edge_counter[ch] / elapsed_s
                    self._edge_counter[ch] = 0
            self._edge_window_t0_ns = now_ns


def reconstruct_timestamp_ns(
    t0_monotonic_ns: int,
    t0_frame_id: int,
    frame_id: int,
    sample_offset: int,
    frame_size: int,
    sample_rate_hz: int,
) -> int:
    base_sample_index = t0_frame_id * frame_size
    sample_index = frame_id * frame_size + sample_offset
    delta_samples = sample_index - base_sample_index
    delta_ns = int((delta_samples * 1_000_000_000) / sample_rate_hz)
    return t0_monotonic_ns + delta_ns
