from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class TTLEdge:
    channel_index: int
    timestamp_sec: float
    sample_index: int
    pulse_width_samples: int
    edge_type: str


def extract_edges_from_payload(
    payload: bytes,
    sample_rate_hz: int,
    frame_size: int,
    frame_id_start: int = 0,
    channels: int = 4,
    last_state: Optional[List[int]] = None,
    rise_at: Optional[Dict[int, int]] = None,
) -> List[TTLEdge]:
    """Extract rising/falling edges and pulse widths from packed bitmask samples."""
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be > 0")

    if last_state is None:
        last_state = [0] * channels
    elif len(last_state) != channels:
        raise ValueError("last_state length must match channels")

    if rise_at is None:
        rise_at = {}
    edges: List[TTLEdge] = []

    for offset, sample in enumerate(payload):
        sample_index = frame_id_start * frame_size + offset
        timestamp_sec = sample_index / float(sample_rate_hz)
        for ch in range(channels):
            state = (sample >> ch) & 0x01
            if state == 1 and last_state[ch] == 0:
                rise_at[ch] = sample_index
                edges.append(
                    TTLEdge(
                        channel_index=ch,
                        timestamp_sec=timestamp_sec,
                        sample_index=sample_index,
                        pulse_width_samples=0,
                        edge_type="rising",
                    )
                )
            elif state == 0 and last_state[ch] == 1:
                width = 0
                if ch in rise_at:
                    width = sample_index - rise_at[ch]
                    del rise_at[ch]
                edges.append(
                    TTLEdge(
                        channel_index=ch,
                        timestamp_sec=timestamp_sec,
                        sample_index=sample_index,
                        pulse_width_samples=width,
                        edge_type="falling",
                    )
                )
            last_state[ch] = state

    return edges
