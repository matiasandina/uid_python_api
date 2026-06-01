import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ttl_capture.frame_index import (
    HEADER_SIZE,
    RECORD_SIZE,
    TTLFrameIndexWriter,
    read_frame_index,
)


class TTLFrameIndexTests(unittest.TestCase):
    def test_round_trip_writes_header_and_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ttl_frames.bin"
            with TTLFrameIndexWriter(path, sampling_rate_hz=20_000, frame_size=2048) as writer:
                writer.append(frame_id=120, t_us_first_sample=61_542_400, payload_offset_bytes=0)
                writer.append(frame_id=121, t_us_first_sample=61_644_800, payload_offset_bytes=2048)
                writer.append(frame_id=123, t_us_first_sample=61_849_600, payload_offset_bytes=4096)

            header, records = read_frame_index(path)
            file_size = path.stat().st_size

        self.assertEqual(header.sampling_rate_hz, 20_000)
        self.assertEqual(header.frame_size, 2048)
        self.assertEqual(header.record_count, 3)
        self.assertEqual(
            [(record.frame_id, record.t_us_first_sample, record.payload_offset_bytes) for record in records],
            [
                (120, 61_542_400, 0),
                (121, 61_644_800, 2048),
                (123, 61_849_600, 4096),
            ],
        )
        self.assertEqual(file_size, HEADER_SIZE + 3 * RECORD_SIZE)


if __name__ == "__main__":
    unittest.main()
