# Teensy TTL Capture

TTL capture is optional and starts when:
- stimulation is enabled
- `stimulus.square.ttl_output=true`
- `ttl_capture.port` is configured

## Runtime Outputs

TTL service writes session artifacts in the session folder:
- `ttl_raw.bin`
- `ttl_frames.bin`
- `ttl_meta.json`

## TTLFRM01 Frame Index

As of 2026-06-01, sessions may include a binary frame index sidecar named `ttl_frames.bin`.

Purpose:

- preserve the original Teensy `frame_id` for each received payload block
- make dropped frames detectable offline without changing `ttl_raw.bin`
- keep legacy readers working by leaving `ttl_raw.bin` and `ttl_meta.json` intact

Compatibility:

- older sessions may contain only `ttl_raw.bin` + `ttl_meta.json`
- readers must continue to support those legacy sessions
- when `ttl_frames.bin` is present, readers should prefer it for timing and gap detection

Format:

- file name: `ttl_frames.bin`
- endianness: little-endian
- header size: `28` bytes
- record size: `20` bytes

Header layout:

- `magic[8]`: ASCII `TTLFRM01`
- `uint16 header_size`
- `uint16 record_size`
- `uint32 sampling_rate_hz`
- `uint16 frame_size`
- `uint16 reserved`
- `uint64 record_count`

Record layout:

- `uint32 frame_id`
- `uint64 t_us_first_sample`
- `uint64 payload_offset_bytes`

Interpretation:

- each record describes one payload block written into `ttl_raw.bin`
- `payload_offset_bytes` points to the first byte of that frame payload inside `ttl_raw.bin`
- missing `frame_id` values indicate dropped frames on the capture path
- `t_us_first_sample` is stored as emitted by the current firmware and may wrap on long sessions, so offline timing should use `frame_id`, `frame_size`, and `sampling_rate_hz` as the primary basis

## Local Utility

Use standalone recorder:

```bash
uv run --python .venv/bin/python scripts/ttl_record.py --port COM7 --duration 30
```

For hardware bring-up, use the live monitor instead of a serial terminal:

```bash
uv run --python .venv/bin/python scripts/ttl_monitor.py --port /dev/ttyACM0
```

Notes:

- firmware sends one text handshake line and then binary frames
- raw samples are not inverted: `1` = Teensy pin HIGH, `0` = Teensy pin LOW
- for the current H11L1 output stage, asserted TTL should appear as `0`

Minimal one-channel troubleshooting sequence:

- confirm Teensy firmware flashes and handshake is visible
- direct jumper test on Teensy `pin 2`:
  - `pin 2 -> 3.3V` should read `CH0=1`
  - `pin 2 -> GND` should read `CH0=0`
- then connect one populated perfboard channel:
  - perfboard `VO` -> Teensy `pin 2`
  - perfboard output-side `3.3V` -> Teensy `3.3V`
  - perfboard output-side `GND` -> Teensy `GND`
- idle `VO` should read `CH0=1`
