# Stimulation Modes

## Timing Layers

There are two different timing layers in this project:

- Pulse timing: the shape of individual pulses sent by the Doric device.
- Train-envelope timing: how long stimulation stays ON before a longer OFF gap.

Today, the runtime uses both layers differently:

- `stimulus.pulse.*` always configures the Doric square pulse waveform.
- `stimulus.train.*` is currently enforced by Python in `stim_controller.py` by starting a channel, waiting `train.on_seconds`, stopping it, then applying a software cooldown for `train.off_seconds`.
- `stimulus.square.nb_of_seq`, `stimulus.square.nb_of_pulses_per_seq`, and `stimulus.square.delay_between_seq_ms` are passed through to the Doric DLL, but the app does not currently derive them automatically from `stimulus.train.*`.

That means the existing default behavior is "hardware pulse shape, software train envelope."

## Closed-Loop

- Each `closed_loop.rules[]` entry owns its own input scope, classifier timing/config, and output channel(s).
- Rules are matched to configured device names/IPs.
- If a rule fires, only that rule's configured output channels are stimulated.
- Optional session-specific RFID assignment can narrow a rule to one or more animals during preflight.
- In `classifier.mode=window`, a `start` event begins the configured software train envelope and repeats `stimulus.train.on_seconds` / `stimulus.train.off_seconds` while the condition remains true. A `stop` event cancels that train and stops the channel.

## Open-Loop

- Finite run window driven by `stimulus.run_for_minutes`.
- Start schedule from `stimulus.start`.
- Target channels from `stimulus.open_loop_assignments[].channel` when assignments are configured, otherwise from `stimulus.target_channels`.
- Optional session-specific RFID assignment can be attached to each open-loop assignment during preflight.
- Train/pulse parameters are applied from `stimulus.train` + `stimulus.pulse`.

Operator rule:

- the open-loop start schedule begins after launch confirmation

As of 2026-04-11, open-loop start supports:

- `mode: immediate`
- `mode: delay` with `delay_seconds`
- `mode: clock` with `timezone`, `at_hhmm`, and optional `rollback_next_day`

### Worked Open-Loop Example

For:

- `run_for_minutes = 1`
- `train.on_seconds = 5`
- `train.off_seconds = 10`

The operator-facing behavior is:

- a 60 second run window
- repeated 5 second ON / 10 second OFF train cycles until the run window ends
- ON epochs beginning at approximately `t=0s`, `t=15s`, `t=30s`, and `t=45s`

This produces four train starts / ON epochs during the run window.

Important distinction:

- the train counter should be read as counting ON epochs, not individual pulses
- `pulse.period_ms` and `pulse.time_on_ms` still describe the fast waveform inside each ON epoch

## Safety Gates

- `stimulus.enabled` must be true to send outputs.
- `stimulus.mode=monitor` keeps the run in observe-only mode.
- `stimulus.mode=laser` allows real hardware output after preflight setup succeeds.
- `ttl_capture.enabled` must be true for laser runs.
- If hardware init fails, outputs remain off for that run.

## Doric Sequence Mapping

For a target waveform of 20 Hz pulses with 10 ms ON / 40 ms OFF, repeated in a 1 s ON / 3 s OFF envelope:

- `stimulus.pulse.period_ms = 50`
- `stimulus.pulse.time_on_ms = 10`
- `stimulus.square.nb_of_pulses_per_seq = 20`
- `stimulus.square.delay_between_seq_ms = 3000`
- `stimulus.square.nb_of_seq = 65535`

Interpretation:

- One pulse every 50 ms gives 20 Hz.
- Twenty pulses per sequence gives a 1 second ON epoch.
- A 3000 ms inter-sequence delay gives the 3 second OFF epoch.
- `65535` is the practical "repeat for a very long time" setting discussed with Doric support.

Important caveats from the vendor clarification:

- `nb_of_seq = 0` should not be used; the minimum valid value is `1`.
- `nb_of_pulses_per_seq = 0` is not a bounded 1 second train. Doric described it as effectively infinite within the sequence, so it should be avoided when you need a finite ON epoch.

## Important: Validate In Doric Studio First

Before copying a sequence design into YAML or Python, validate it in Doric Neuroscience Studio first.

- Doric Studio provides a signal preview, which is the easiest way to confirm that `period`, `time ON`, `pulses per sequence`, `delay between sequences`, and trigger mode produce the waveform you actually intend.
- This is especially important for sequence semantics such as `nb_of_pulses_per_seq`, `delay_between_seq_ms`, `nb_of_seq`, and `Gated + Restart`, where a small misunderstanding can produce a materially different train.
- After the waveform looks correct in the Doric GUI, transfer the same settings into code through `ttlModulation` and related DLL/API parameters.

The local UI is still useful:

- Use Doric Studio to validate the waveform design visually before implementation.
- Use this app's preflight and live UI to confirm the runtime is using the expected configuration during acquisition.

## When To Prefer Hardware

Prefer Doric-native sequencing when:

- You want the 1 s ON / 3 s OFF envelope to continue with hardware timing after a single start command.
- You care more about repeatable envelope timing than about Python having fine-grained control over each ON/OFF boundary.
- You may later synchronize the light source from an external trigger.

Prefer Python-timed envelopes when:

- The classifier should directly decide both ON and OFF transitions in real time.
- You want the app to stop the train immediately when the computed condition becomes false.
- You are still iterating on trigger semantics and do not want the hardware sequence to keep running after a single start.

## Recommended Project Interpretation

If the project goal is "prefer hardware when possible," the clean interpretation is:

- Keep `stimulus.pulse.*` as the user-facing pulse description.
- Configure Doric sequence fields explicitly when the intended train envelope is fixed.
- Treat `stimulus.train.*` as documentation of the desired envelope unless and until the controller is updated to derive and enforce the Doric sequence settings automatically.

Until that controller change is made, setting only `stimulus.train.*` does not move the train envelope into hardware by itself.
