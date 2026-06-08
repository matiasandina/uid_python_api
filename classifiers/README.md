# Classifier Plugins

Classifier functions should expose:

```python
def evaluate(animal_id, window_readings, now, config) -> dict | None:
    ...
```

## Threshold Duration Classifier

Use `classifiers.threshold_duration:evaluate` for threshold rules that should not fire until enough time and enough samples have been observed. Set `direction: "below"` or `direction: "above"` in classifier config. The examples below use `below`.

`observed window coverage` means: within the readings the scheduler passed to the classifier, how much real clock time do those readings span?

Example config:

```yaml
clf_data_input_window_seconds: 30.0
config:
  direction: "below"
  threshold_c: 33.0
  required_duration_seconds: 30.0
  min_samples: 3
  coverage_tolerance_seconds: 1.0
  aggregation: "mean"
```

The classifier receives all readings from the last 30 seconds. Then it checks:

```text
observed_duration = newest_reading_time - oldest_reading_time
```

So if the probe has only just appeared:

```text
10:00:00  32.0 C
10:00:05  32.1 C
```

Then:

```text
observed_duration = 5 seconds
min_samples = 2
```

Even though the mean is below 33, it does **not** trigger, because coverage is too short and samples are below 3.

If later the readings look like:

```text
10:00:00  32.0 C
10:00:15  32.2 C
10:00:30  32.1 C
```

Then:

```text
observed_duration = 30 seconds
sample_count = 3
mean = 32.1 C
```

Now it can trigger, because both are true:

```text
observed_duration >= required_duration_seconds
sample_count >= min_samples
```

In practice, exact window boundaries are discrete. A 30 second scheduler window may contain samples spanning 29.5 seconds because the oldest sample just inside the window is not exactly 30.000 seconds old. `coverage_tolerance_seconds` handles that boundary effect. With the default tolerance of 1.0 second, `observed_duration = 29.5` can satisfy a 30 second rule, but `observed_duration = 20` still cannot.

For 5 minutes:

```yaml
clf_data_input_window_seconds: 300.0
config:
  direction: "below"
  threshold_c: 35.0
  required_duration_seconds: 300.0
  min_samples: 20
  coverage_tolerance_seconds: 1.0
  aggregation: "mean"
```

This means: do not even consider triggering until the animal/probe has readings spanning 5 minutes and at least 20 readings are present. Then trigger if the mean of that window is below 35 C.

`min_samples` protects against sparse data. For example, two readings 5 minutes apart could satisfy coverage, but that is weak evidence:

```text
10:00:00  34.5 C
10:05:00  34.2 C
```

Coverage is 300 seconds, but sample count is only 2. With `min_samples: 20`, this does not trigger.

Runtime status phrases are intentionally short:

- `waiting for samples`: not enough samples for the rule yet
- `collecting window`: enough samples are present, but observed time coverage is still too short
- `window ready; mean below threshold`: the configured aggregation satisfies the threshold
- `window ready; mean not below threshold`: the configured aggregation does not satisfy the threshold
