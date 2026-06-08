As of 2026-06-08, these profiles are intended for closed-loop bench checks and threshold-triggered laser runs.

- machine-local values such as reader hosts, Doric DLL path, Doric UID/port, and preferred TTL port belong in `config.local.yaml`
- closed-loop input routing lives under `closed_loop.rules[].devices`
- closed-loop output routing lives under `closed_loop.rules[].outputs.laser_channels`
- `stimulus.mode: monitor` records classifier decisions and displays them in the live UI, but does not send laser output
- `stimulus.mode: laser` sends configured output channels when trigger events fire
- `data.averaging_window_seconds` must be at least as long as the classifier input window; these profiles set it slightly longer than the classifier window
- avoid very long troubleshooting windows because the classifier cannot fire until the required window has elapsed

Profiles:

- `closedloop_monitor_below33_30s.yaml`: monitor-only check; starts when the 30 second mean is below 33 C
- `closedloop_laser_below33_30s.yaml`: same below-33 C check, but with real laser output and TTL capture enabled
- `closedloop_laser_below35_5min.yaml`: 5 minute below-35 C mean profile for closer experiment logic
- `stim_below35.yaml`: legacy/simple closed-loop profile using the basic average-below-threshold classifier with a 1 second input window

Classifier timing:

- `closed_loop.rules[].classifier.clf_data_input_window_seconds` controls which recent readings are passed to the classifier
- `classifiers.threshold_duration:evaluate` also checks `required_duration_seconds` before allowing a trigger
- for a true all-observed-samples-below rule, set `aggregation: "all"`
- for the mean-over-window proxy, set `aggregation: "mean"`
- see `classifiers/README.md` and `user_guide/closed-loop.qmd` for worked examples of observed window coverage and `min_samples`
