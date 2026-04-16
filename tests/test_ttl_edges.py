import unittest

from ttl_capture.edges import extract_edges_from_payload


class TTLEdgeTests(unittest.TestCase):
    def test_known_sequence_edges_and_width(self):
        # Channel 0: low low high high high low low
        payload = bytes([0b0000, 0b0000, 0b0001, 0b0001, 0b0001, 0b0000, 0b0000])
        edges = extract_edges_from_payload(
            payload=payload,
            sample_rate_hz=20000,
            frame_size=2048,
            frame_id_start=0,
            channels=4,
        )

        self.assertEqual(len(edges), 2)
        self.assertEqual(edges[0].edge_type, "rising")
        self.assertEqual(edges[0].channel_index, 0)
        self.assertEqual(edges[0].sample_index, 2)

        self.assertEqual(edges[1].edge_type, "falling")
        self.assertEqual(edges[1].channel_index, 0)
        self.assertEqual(edges[1].sample_index, 5)
        self.assertEqual(edges[1].pulse_width_samples, 3)

    def test_no_spurious_rising_edge_when_next_frame_starts_high(self):
        last_state = [0, 0, 0, 0]
        rise_at = {}

        first_payload = bytes([0b0000, 0b0001, 0b0001])
        first_edges = extract_edges_from_payload(
            payload=first_payload,
            sample_rate_hz=20000,
            frame_size=3,
            frame_id_start=0,
            channels=4,
            last_state=last_state,
            rise_at=rise_at,
        )

        second_payload = bytes([0b0001, 0b0001, 0b0000])
        second_edges = extract_edges_from_payload(
            payload=second_payload,
            sample_rate_hz=20000,
            frame_size=3,
            frame_id_start=1,
            channels=4,
            last_state=last_state,
            rise_at=rise_at,
        )

        self.assertEqual(len(first_edges), 1)
        self.assertEqual(first_edges[0].edge_type, "rising")
        self.assertEqual(first_edges[0].sample_index, 1)

        self.assertEqual(len(second_edges), 1)
        self.assertEqual(second_edges[0].edge_type, "falling")
        self.assertEqual(second_edges[0].sample_index, 5)
        self.assertEqual(second_edges[0].pulse_width_samples, 4)


if __name__ == "__main__":
    unittest.main()
