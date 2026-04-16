# Teensy TTL Capture

TTL capture is optional and starts when:
- stimulation is enabled
- `stimulus.square.ttl_output=true`
- `ttl_capture.port` is configured

## Runtime Outputs

TTL service writes session artifacts in the session folder:
- `ttl_raw.bin`
- `ttl_meta.json`

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
