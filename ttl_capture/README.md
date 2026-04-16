# ttl_capture

Python ingestion and logging for Teensy TTL frames.

## Components

- `reader.py`: `TeensyTTLReader` serial handshake + frame iteration
- `protocol.py`: handshake and frame parsing utilities with re-sync
- `capture.py`: background capture service writing `ttl_raw.bin` + `ttl_meta.json`
- `edges.py`: in-memory edge extraction helper (no parquet output yet)

## Session Outputs

Inside a session folder:

- `ttl_raw.bin`: concatenated frame payload bytes
- `ttl_meta.json`: metadata (sample rate, frame size, channel map, start times, hashes)

## Dependency Notes

Required:

- `pyserial`

Optional (future):

- `pyarrow` for parquet edge export (deferred)
