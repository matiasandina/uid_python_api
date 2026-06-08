# Closed-Loop Config Builder Skill

Use this skill when the user wants to build, adapt, or sanity-check an experiment config from conversational intent, especially closed-loop temperature-triggered stimulation. Keep the flow short: start from an existing config in `configs/`, ask only for missing decisions, write a focused YAML overlay, then validate the full resolved config.

## Operating Rule

Do not build configs from scratch unless the user explicitly asks. First choose the closest base profile:

- bench monitor, below 33 C, 30 s: `configs/closed_loop/closedloop_monitor_below33_30s.yaml`
- bench laser, below 33 C, 30 s: `configs/closed_loop/closedloop_laser_below33_30s.yaml`
- experiment-like laser, below 35 C, 5 min: `configs/closed_loop/closedloop_laser_below35_5min.yaml`
- simple legacy closed loop: `configs/closed_loop/stim_below35.yaml`
- open-loop profiles: `configs/open_loop/*.yaml`

Load the base file, summarize its intent in plain language, then ask for deltas. If the user gives enough intent, choose conservative defaults and show assumptions before editing.

## Question Flow

Ask questions in this order. Stop asking once the answer can be inferred from the base profile or a previous user statement.

1. Run type
   - monitor-only, laser closed loop, or open loop
   - bench/probe test or real animal run

2. Subjects and routing
   - number of animals/probes/rules
   - reader token for each rule, usually a configured device name like `Reader-1`
   - output channel for each rule, usually `ch1`
   - animal/probe RFID assignment is normally left to preflight; closed loop must not launch with zero assigned RFIDs

3. Classifier intent
   - direction: `below` or `above`
   - threshold temperature in C
   - evidence duration: how long the condition should be supported before stimulation starts
   - aggregation: `mean`, `all`, or `fraction`

4. Evidence quality
   - expected sample cadence, if known
   - derive `min_samples`; for a 30 s troubleshooting profile, prefer `4`
   - derive `clf_data_input_window_seconds`; keep it longer than `required_duration_seconds`
   - keep `coverage_tolerance_seconds` small; default `1.0`

5. Stimulation
   - `stimulus.mode`: `monitor` or `laser`
   - pulse period and pulse width
   - train envelope, e.g. `on_seconds: 1.0`, `off_seconds: 3.0`
   - current per channel
   - TTL capture should be enabled for `stimulus.mode: laser`

6. Missing data and stop behavior
   - window-mode stimulation stops when there are no readings left in the classifier input window
   - `missing_animal_seconds` is the longer alarm/log threshold
   - choose `missing_animal_seconds` to reflect when the user wants a missing-animal event recorded, not the first safety stop

## Derived Defaults

For a 30 s below-threshold troubleshooting profile:

```yaml
closed_loop:
  rules:
    - classifier:
        evaluate_interval_seconds: 1.0
        clf_data_input_window_seconds: 45.0
        missing_animal_seconds: 120.0
        mode: "window"
        config:
          direction: "below"
          threshold_c: 33.0
          required_duration_seconds: 30.0
          min_samples: 4
          coverage_tolerance_seconds: 1.0
          aggregation: "mean"
```

For a 5 min experiment-like profile:

```yaml
closed_loop:
  rules:
    - classifier:
        evaluate_interval_seconds: 5.0
        clf_data_input_window_seconds: 330.0
        missing_animal_seconds: 120.0
        mode: "window"
        config:
          direction: "below"
          threshold_c: 35.0
          required_duration_seconds: 300.0
          min_samples: 20
          coverage_tolerance_seconds: 1.0
          aggregation: "mean"
```

Make `data.averaging_window_seconds` at least as long as `clf_data_input_window_seconds`; add extra margin.

## Summary Before Editing

Before changing YAML, show a compact summary:

```text
Base: configs/closed_loop/closedloop_laser_below33_30s.yaml
Rules: 1 rule, Reader-1 -> ch1
Condition: mean temperature below 33 C for 30 s
Evidence: 45 s input buffer, 4 samples minimum, 1 s tolerance
Output: laser mode, 20 Hz pulse, 10 ms width, 1 s ON / 3 s OFF
Missing data: stop when no evidence remains; log missing after 120 s
Assignments: RFID selected at preflight; zero assigned RFIDs are not allowed
```

If the user wants the edit, copy the base profile to a new overlay path under `configs/closed_loop/` or edit the named profile directly if requested. Keep machine-local fields out of overlays; device hosts, Doric DLL path, Doric UID/port, and serial ports belong in `config.local.yaml`.

## Validation

After editing, validate the full resolved config, not only the changed YAML.

If `config.local.yaml` exists, validate the selected overlay against it:

```bash
.venv/bin/python -c "from typed_config import load_config; load_config('configs/closed_loop/PROFILE.yaml', require_local=True); print('config ok')"
```

Always run the focused repo overlay test:

```bash
.venv/bin/python -m pytest tests/test_typed_config.py::TypedConfigTests::test_repo_overlays_load_with_minimal_machine_local_config
```

For classifier or scheduler changes, also run:

```bash
.venv/bin/python -m pytest tests/test_threshold_duration_classifier.py tests/test_trigger_scheduler.py tests/test_stim_controller.py
```

If validation fails, fix the config rather than explaining around it. If failure is due to missing machine-local values, say exactly which field belongs in `config.local.yaml`.

## Guardrails

- Do not ask for every YAML parameter. Start from a profile and ask only for decisions that change the profile.
- Do not remove `direction`; classifier direction must support both `below` and `above`.
- Do not set `clf_data_input_window_seconds` equal to `required_duration_seconds` for sparse or jittery data.
- Do not set `data.averaging_window_seconds` shorter than classifier input windows.
- Do not allow closed-loop laser output with no assigned RFIDs.
- Do not enable `stimulus.mode: laser` without `ttl_capture.enabled: true`.
- Do not put machine-local values into protocol overlays.
