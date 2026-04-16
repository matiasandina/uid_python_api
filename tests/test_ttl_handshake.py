import unittest

from ttl_capture.protocol import (
    HandshakeError,
    parse_handshake_line,
    validate_handshake,
)


class TTLHandshakeTests(unittest.TestCase):
    def test_accepts_expected_handshake(self):
        raw = (
            b'#TTL_HANDSHAKE {"version":1,"sampling_rate_hz":20000,'
            b'"frame_size":2048,"channel_map":[1,2,3,4],"firmware_version":"x","git_hash":"abc"}\n'
        )
        hs = parse_handshake_line(raw)
        validate_handshake(hs)

    def test_rejects_sampling_rate_mismatch(self):
        raw = (
            b'#TTL_HANDSHAKE {"version":1,"sampling_rate_hz":10000,'
            b'"frame_size":2048,"channel_map":[1,2,3,4],"firmware_version":"x","git_hash":"abc"}\n'
        )
        hs = parse_handshake_line(raw)
        with self.assertRaises(HandshakeError):
            validate_handshake(hs)

    def test_rejects_frame_size_mismatch(self):
        raw = (
            b'#TTL_HANDSHAKE {"version":1,"sampling_rate_hz":20000,'
            b'"frame_size":1024,"channel_map":[1,2,3,4],"firmware_version":"x","git_hash":"abc"}\n'
        )
        hs = parse_handshake_line(raw)
        with self.assertRaises(HandshakeError):
            validate_handshake(hs)


if __name__ == "__main__":
    unittest.main()
