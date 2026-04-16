import struct
import unittest

from ttl_capture.protocol import FRAME_MAGIC, TTLFrameParser


def build_frame(frame_id: int, payload: bytes, t_us: int = 1234) -> bytes:
    return FRAME_MAGIC + struct.pack("<IHQ", frame_id, len(payload), t_us) + payload


class TTLFrameParserTests(unittest.TestCase):
    def test_magic_split_across_reads(self):
        parser = TTLFrameParser()
        payload = bytes([0x01, 0x02, 0x03])
        frame = build_frame(7, payload)

        out1 = parser.feed(frame[:1])
        out2 = parser.feed(frame[1:5])
        out3 = parser.feed(frame[5:])

        self.assertEqual(out1, [])
        self.assertEqual(out2, [])
        self.assertEqual(len(out3), 1)
        self.assertEqual(out3[0].frame_id, 7)
        self.assertEqual(out3[0].payload, payload)

    def test_noise_preamble_then_sync(self):
        parser = TTLFrameParser()
        payload = bytes([0x0F, 0x00])
        stream = b"\x99\x98\x97garbage" + build_frame(3, payload)
        frames = parser.feed(stream)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].frame_id, 3)
        self.assertEqual(frames[0].payload, payload)

    def test_truncated_frame_then_remainder(self):
        parser = TTLFrameParser()
        payload = bytes([0x01] * 8)
        frame = build_frame(11, payload)

        first = parser.feed(frame[:10])
        second = parser.feed(frame[10:])

        self.assertEqual(first, [])
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].frame_id, 11)

    def test_dropped_mid_frame_bytes_resync_to_next_magic(self):
        parser = TTLFrameParser()

        good1 = build_frame(1, bytes([1, 2, 3, 4]))
        bad = bytearray(build_frame(2, bytes([5, 6, 7, 8])))
        bad[6] = 0xFF
        bad[7] = 0xFF  # Corrupt n_samples to force parser re-sync.
        good2 = build_frame(3, bytes([9, 10, 11, 12]))

        frames = parser.feed(good1 + bytes(bad) + good2)
        self.assertEqual([f.frame_id for f in frames], [1, 3])


if __name__ == "__main__":
    unittest.main()
