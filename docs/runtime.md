# Runtime Overview

## Core Flow

1. `main.py` loads a typed runtime config using:
   - minimal safe schema defaults
   - `config.local.yaml`
   - optional `--config` overlay
2. Preflight prompts user to validate and launch.
3. Preflight may update real run values in config, but runtime-only objects stay in separate live state.
3. `DeviceManager` starts:
   - TCP device connections
   - health monitor
   - stimulation control plane
   - optional TTL capture service
4. Raw packets are parsed and logged.
5. Registry/UI are updated continuously.

## Config Boundary

- `config.example.yaml` is documentation only.
- `config.local.yaml` contains machine-local values.
- overlays under `configs/` contain experiment/protocol values.
- overlays must not define machine-local fields such as device endpoints, DLL paths, Doric UID, or preferred ports.

Load-time validation catches:
- schema/type errors
- invalid enum values
- unknown stimulus channel names
- contradictory stimulation/trigger settings
- missing required-by-mode fields

Launch/setup validation catches:
- unresolved live Doric device identity for laser runs
- preflight completion requirements
- hardware reachability

Session metadata records the final config that actually ran.

## Key Modules

- `typed_config.py`: typed schema, merge policy, and validation
- `live_state.py`: runtime-only setup/live objects
- `device_manager.py`: orchestration and lifecycle
- `ip_device.py`: TCP receive loop
- `data_parser.py`: packet parsing
- `datalogger_csv.py`: per-device logging
- `trigger_scheduler.py`: classifier scheduling (closed-loop)
- `stim_controller.py`: stimulation dispatch and safety gates
- `ttl_capture/`: Teensy serial ingest pipeline
