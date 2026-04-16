As of 2026-03-24, these profiles are intended for fixed-frequency open-loop laser runs.

- `stimulus.start` begins after the preflight arm/start confirmation.
- For relative starts, use `stimulus.start.mode: delay` with `stimulus.start.delay_seconds`.
- For wall-clock starts, use `stimulus.start.mode: clock` with `stimulus.start.timezone`, `stimulus.start.at_hhmm`, and optional `stimulus.start.rollback_next_day`.
- `run_for_minutes: 240.0` gives a 4 hour run window.
- `train.on_seconds: 1.0` / `train.off_seconds: 3.0` gives a 1 s ON / 3 s OFF duty cycle (25%), matching the closed-loop default.
- `pulse.time_on_ms: 10` sets 10 ms pulse width.
- `stimulus.open_loop_assignments[]` is the preferred shape for these profiles.
- each assignment should define:
  - `id`: human-facing slot label shown in preflight and analysis
  - `device`: configured reader token used for RFID discovery in preflight
  - `channel`: output channel to stimulate
- the shipped defaults use one slot on `device: Reader-1` and `channel: ch1`; edit `id`, `device`, and `channel` to match your rig.
- machine-local values such as `stimulus.dll_path`, Doric UID/port, and preferred TTL port belong in `config.local.yaml`.
- these overlays may define `stimulus.channels` when channel selection and current are protocol-specific.
- pulse timing is defined under `stimulus.pulse.*`; the runtime derives the matching Doric square timing internally.
- if `train.off_seconds > 0`, the run repeats ON/OFF train epochs inside the outer run window; the live counter should be interpreted as counting train starts / ON epochs, not individual pulses.
- the open-loop schedule starts after launch confirmation in preflight, not when the app first opens.

Worked interpretation example:

- `run_for_minutes = 1`
- `train.on_seconds = 5`
- `train.off_seconds = 10`

This gives a 60 second run window with repeated 5 s ON / 10 s OFF train cycles, for four ON epochs total.

Profiles:

- `openloop_1hz_troubleshoot.yaml`: 1 Hz, 30 second delayed start, 1 minute run for bench checks
- `openloop_5hz.yaml`: 5 Hz, 5 minute delayed start
- `openloop_10hz.yaml`: 10 Hz, 5 minute delayed start
- `openloop_20hz.yaml`: 20 Hz, 5 minute delayed start
