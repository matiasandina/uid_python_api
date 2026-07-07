# Config Guide

## Main File

As of 2026-04-03, `config.example.yaml` is documentation only.

Runtime merge order:
- minimal safe schema defaults
- `config.local.yaml` for machine-specific values
- optional `--config` overlay for the selected run protocol

File roles:
- `config.local.yaml`: machine-specific values such as device endpoints, Doric DLL path, Doric UID, fallback port, and discovery settings
- `configs/...`: protocol and experiment overlays
- `config.example.yaml`: template/reference, not runtime input

Overlay policy:
- overlays must not define machine-local fields
- overlays should contain only experiment/protocol values, including `data.averaging_window_seconds` for closed-loop history retention and `stimulus.channels` when channel selection and current are run-specific
- if an overlay includes machine-local fields, loading fails

## High-Impact Fields

### Acquisition
- `devices[]`: `{host, port, name}`
- `network.*`: socket/reconnect/stale monitor timings
- `data.averaging_window_seconds`: retained history window

### Closed-Loop
- `closed_loop.rules[]`
- `closed_loop.rules[].id`
- `closed_loop.rules[].devices`: configured device names or IPs that feed this rule
- `closed_loop.rules[].classifier.plugin`
- `closed_loop.rules[].classifier.evaluate_interval_seconds`
- `closed_loop.rules[].classifier.clf_data_input_window_seconds`
- `closed_loop.rules[].classifier.missing_animal_seconds`
- `closed_loop.rules[].classifier.missing_animal_stop_clf_seconds`: optional stop threshold; defaults to `missing_animal_seconds` when omitted
- `closed_loop.rules[].classifier.mode`: `pulse` or `window`
- `closed_loop.rules[].classifier.config`
- `closed_loop.rules[].outputs.laser_channels`
- `closed_loop.rules[].assigned_animal_ids`: optional runtime/session assignment populated during preflight

### Stimulation
- `stimulus.enabled`
- `stimulus.mode`: `monitor` or `laser`
- `stimulus.control_mode`: `closed_loop` or `open_loop`; leave unset when stimulation is inactive
- `stimulus.channels`: logical channel mapping plus per-channel current
- `stimulus.dll_path`: optional Doric DLL file or containing directory. As of 2026-04-08, the default is the repo-local, gitignored `./vendor/DoricSystemDLL` directory.
- `stimulus.uid`: Doric laser UID. As of 2026-04-07, this is preferred with the newer Doric DLL and skips numeric port probing.
- `stimulus.port`: fallback numeric Doric USB port for older DLLs or rigs that still use port access
- `stimulus.discovery.*`: fallback Doric DLL port probe candidates/range and optional Windows USB VID/PID diagnostics
- `stimulus.pulse.*`: waveform period/width
- `stimulus.train.*`: ON/OFF envelope
- `stimulus.square.*`: raw Doric square/sequence parameters sent to the DLL

Notes:

- `stimulus.pulse.*` is the canonical pulse description used by the app and copied into `stimulus.square.period_ms` / `stimulus.square.time_on_ms`.
- If `stimulus.uid` is set, the runtime uses the Doric `_uid` DLL functions such as `open_device_uid`, `ls_send_settings_uid`, and `ls_start_channel_uid`. If `stimulus.uid` is unset, the runtime keeps the existing `stimulus.port` path.
- To populate the default DLL directory, run `uv run --python .venv/bin/python scripts/setup_doric_dll.py`. The script downloads Doric's package from `https://doriclenses.com/downloads/.updates/softwares/DoricSystemDLL.zip`, unpacks it under `./vendor/DoricSystemDLL`, and prints the resolved DLL path. Normal app startup does not use network access.
- To find the UID without opening Doric Neuroscience Studio, run `scripts/doric_probe.py --dll-path C:/Doric/DoricSystem.dll --list-infos`. As of 2026-04-08, `--dll-path` may be either the exact `DoricSystem.dll` file or a containing directory such as `C:/Users/HRV/Downloads/DoricSystemDLL`; the resolver searches recursively and prefers an `x64` DLL. The newer Doric manual says `available_devices_infos()` prints detected device names, ports, and unique IDs through the DLL debug output.
- As of 2026-04-08, GitHub issue #7 records that the 2026 package may require `dcamapi.dll`. Run `scripts/doric_imports_diag.py ./vendor/DoricSystemDLL` after setup; if `dcamapi.dll` is missing, use the known-good 2024 Doric package or install the vendor-required DCAM runtime and set `stimulus.dll_path` explicitly.
- `stimulus.discovery.probe_wait_ms` defaults to `20` for port probing only. The actual laser connection path still uses the longer Doric settle waits before arming.
- `stimulus.train.*` describes the intended ON/OFF train envelope, but the current runtime still enforces that envelope in Python.
- `stimulus.square.nb_of_seq`, `stimulus.square.nb_of_pulses_per_seq`, and `stimulus.square.delay_between_seq_ms` are the knobs that move train repetition into Doric hardware.
- For the common 20 Hz, 10 ms ON, 1 s ON / 3 s OFF pattern, use:
  `period_ms=50`, `time_on_ms=10`, `nb_of_pulses_per_seq=20`, `delay_between_seq_ms=3000`, `nb_of_seq=65535`.
- As of 2026-04-11, closed-loop routing is defined per rule under `closed_loop.rules[]` rather than by the old global `triggers.target_channels` model.

### Open-Loop
- `stimulus.target_channels`
- `stimulus.open_loop_assignments[]`
- `stimulus.open_loop_assignments[].id`
- `stimulus.open_loop_assignments[].device`
- `stimulus.open_loop_assignments[].channel`
- `stimulus.open_loop_assignments[].assigned_animal_ids`: optional runtime/session assignment populated during preflight
- `stimulus.run_for_minutes`
- `stimulus.start.mode`: `immediate`, `delay`, or `clock`
- `stimulus.start.delay_seconds`
- `stimulus.start.timezone`
- `stimulus.start.at_hhmm`
- `stimulus.start.rollback_next_day`

Notes:

- As of 2026-04-11, `stimulus.start` is the preferred scheduling shape for open-loop runs.
- `stimulus.start_delay_seconds` still works as a deprecated fallback and is translated to `stimulus.start.mode=delay` at runtime.
- As of 2026-04-15, `stimulus.open_loop_assignments[]` is the preferred way to define named open-loop output slots that can receive session-specific RFID assignment during preflight.
- `stimulus.open_loop_assignments[].device` must match a configured device token from `devices[]`, typically the reader `name`.
- `stimulus.target_channels` remains supported as a legacy fallback when no open-loop assignments are defined.

### TTL Capture
- `ttl_capture.enabled`
- `ttl_capture.port`: selected during preflight for laser runs
- `ttl_capture.baudrate`
- `ttl_capture.serial_timeout_seconds`
- `ttl_capture.read_chunk_bytes`

## Runtime State

Preflight and live setup keep runtime-only state outside the saved config.

Examples:
- live Doric driver object
- setup progress flags
- temporary probe results

Preflight may still update real config values that define what actually ran, such as the resolved stimulus or TTL port selected for the session.
