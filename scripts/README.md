# Scripts

## `setup_doric_dll.py`

As of 2026-04-08, the app defaults `stimulus.dll_path` to the repo-local,
gitignored `./vendor/DoricSystemDLL` directory. Download and unpack Doric's DLL
package there with:

```bash
uv run --python .venv/bin/python scripts/setup_doric_dll.py
```

The script does not run during normal acquisition and does not commit vendor
DLLs. It prints the resolved `DoricSystem.dll` path plus follow-up diagnostic
commands.

As of 2026-04-08, the 2026 package may fail to load if `dcamapi.dll` is missing
from the machine. Run `scripts/doric_imports_diag.py` after setup; if it reports
`dcamapi.dll` missing, use the known-good 2024 Doric package or install the
vendor-required DCAM runtime and set `stimulus.dll_path` explicitly.

## `doric_probe.py`

Doric DLL connectivity probe for Windows hardware bring-up.

To list detected devices without opening Doric Neuroscience Studio, ask the DLL
to print its device info table:

```bash
uv run scripts/doric_probe.py --dll-path C:/Doric/DoricSystem.dll --list-infos
```

As of 2026-04-08, `--dll-path` can be the exact `DoricSystem.dll` file or a
containing directory. For example, the April 2026 download can be probed from
the parent folder:

```bash
uv run scripts/doric_probe.py --dll-path "C:/Users/HRV/Downloads/DoricSystemDLL" --list-infos
```

The Doric manual describes `available_devices_infos()` as printing detected
device names, ports, and unique IDs. If that output appears in the console, copy
the laser UID into `config.local.yaml` as `stimulus.uid`.

As of 2026-04-07, prefer probing by Doric UID when using the newer DLL:

```bash
uv run scripts/doric_probe.py --dll-path C:/Doric/DoricSystem.dll --uid c006855a20e326fe
```

Fallback numeric port probing is still available:

```bash
uv run scripts/doric_probe.py --dll-path C:/Doric/DoricSystem.dll --ports 1,2,3,4,5,6,7,8 --wait-ms 20
```

## `calibrate_laser_channel.py`

Manual continuous-light channel calibration helper for Doric output.

As of 2026-04-09, the script:
- keeps the Doric device connected across the full sweep
- re-sends per-channel settings between steps without reconnecting
- runs continuous (`CW`) output for a fixed duration per current step
- prompts for manual power-meter readings in mW
- prints both linear and power-law fits plus the solved current for the target mW

Typical use:

```bash
uv run scripts/calibrate_laser_channel.py --channel ch1
```

Defaults:
- `--target-mw 10`
- `--currents 30 40 50 60 70 80`
- `--duration-sec 10`
- machine-local Doric settings loaded from `config.local.yaml`

Optional overlay:

```bash
uv run scripts/calibrate_laser_channel.py --channel ch1 --config configs/open_loop/openloop_20hz.yaml
```

## `ttl_record.py`

Standalone Teensy TTL recorder for hardware bring-up and debugging.

Use this when you want to validate the TTL USB pipeline without running the full
application (`main.py`), Doric control, trigger scheduler, or TCP device ingest.

### What It Does

- Opens a Teensy USB CDC serial port.
- Waits for and validates the Teensy handshake header.
- Records framed TTL payloads for a fixed duration.
- Writes:
  - `ttl_raw.bin` (raw packed 4-channel TTL samples)
  - `ttl_meta.json` (session metadata + handshake info)

### Typical Use Cases

- First-time Teensy bring-up on a new machine.
- Verifying serial port/driver behavior.
- Confirming framing/sync is stable before app integration.
- Troubleshooting capture issues separately from stimulation logic.

### Prerequisites

- Python environment with dependencies installed:
  - `pyserial`
- Teensy running the `teensy_ttl_sampler` firmware.

### Run

```bash
uv run --python .venv/bin/python scripts/ttl_record.py --port COM7 --duration 30
```

Linux example:

```bash
uv run --python .venv/bin/python scripts/ttl_record.py --port /dev/ttyACM0 --duration 30
```

### Output Location

By default, output goes under `./ttl_recordings/<timestamp>_ttl_record/`.
You can override with `--output-dir`.

### Interpreting Output

- `Frames received > 0` and `Bytes written > 0`: capture path is active.
- `Dropped frames > 0`: host could not keep up or stream had interruptions.
- `Last error` present: inspect serial port, firmware handshake, or cable/power.

### Full App vs Script

- `scripts/ttl_record.py`: TTL-only debugging.
- `main.py`: full monitoring + stimulation orchestration.

## `ttl_monitor.py`

Minimal live/raw inspector for Teensy TTL payloads.

Use this when you want readable GPIO states instead of binary serial noise.

### Live Monitor

```bash
uv run --python .venv/bin/python scripts/ttl_monitor.py --port /dev/ttyACM0
```

This prints:
- handshake details
- latest raw GPIO state every ~0.5 s
- final summary on Ctrl+C:
  - frame count
  - per-channel high/low counts
  - per-channel transition counts

Raw semantics are not inverted:
- `1` = Teensy pin HIGH
- `0` = Teensy pin LOW

For the current H11L1 board, asserted TTL should appear as `0`.

### Offline Summary

```bash
uv run --python .venv/bin/python scripts/ttl_monitor.py --raw-file ttl_recordings/.../ttl_raw.bin
```

## `ttl_verify_session.py`

Post-run verifier for live session artifacts.

Use this after a laser run to compare commanded open-loop windows from the
session metadata YAML against observed TTL edges in the session folder.

### Run

```bash
uv run --python .venv/bin/python scripts/ttl_verify_session.py --session-metadata data/<session>/session.yaml
```

This reports:
- per-channel rising-edge counts
- inferred pulse frequency from TTL edges
- whether each commanded open-loop window had TTL activity inside it
- whether any rising edges happened outside commanded windows

Important limitation:
- precise window matching requires event records with monotonic timestamps
- this script is most useful for sessions recorded after the metadata/event persistence updates
