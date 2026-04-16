# Analysis Tools

As of 2026-04-15, the in-repo analysis layer is focused on loading and normalizing
session artifacts, not plotting.

## Scope

The analysis package reads one session folder and produces normalized tables from:

- temperature CSV files
- `session.yaml`
- `ttl_meta.json`
- `ttl_raw.bin`

Current package entrypoints:

- `analysis_tools.load_analysis_session(...)`
- `python -m analysis_tools.cli`

## Time Handling

Temperature CSV timestamps are treated as local acquisition time.

The analysis layer keeps both:

- `timestamp_local`
- `timestamp_utc`

This is intentional:

- local time is needed to interpret protocol settings such as `stimulus.start.at_hhmm`
- UTC is needed for stable merges across sessions and machines

If the session metadata does not contain a resolvable timezone, pass one explicitly:

```bash
uv run python -m analysis_tools.cli data/<session_dir> --local-timezone America/New_York
```

Timezone resolution order:

1. explicit CLI / function override
2. `session.local_timezone`
3. `config.local_timezone`
4. `config.stimulus.start.timezone`

As of 2026-04-15, the analysis CLI writes parquet outputs by default under
`<session>/analysis/`. Use `--output-dir` only when you want a different target.

## Output Tables

The current normalized tables are:

- `session`
- `temperature`
- `trigger_events`
- `stimulation_windows`
- `ttl_edges`
- `ttl_pulses`
- `temperature_annotated`

`temperature_annotated` adds stimulation-window annotations to each temperature row.

## Temperature Duplicate Policy

As of 2026-04-15, exact duplicate temperature timestamps are dropped per animal ID.

Deduplication key:

- `animal_id`
- `timestamp_utc_ns`

Policy:

- keep the first observed row
- allow different animals to share the same timestamp
- merge all device CSVs into one session-level temperature table first, then deduplicate

This is intentionally session-oriented rather than CSV-oriented.

## TTL Time Semantics

TTL decoding preserves monotonic timing and also exposes a best-effort wall-clock
estimate when `ttl_meta.json` contains enough information to anchor it.

Use:

- `timestamp_monotonic_ns` for precise within-session TTL timing
- `timestamp_estimated_utc` as an approximate wall-clock anchor
- `ttl_pulses` as the compact encoded representation for downstream storage and analysis

For strict "did TTL activity occur inside the commanded window" checks, continue to
use the verification logic in `ttl_capture/session_verify.py`.

## Raw TTL Storage Contract

The analysis layer may read `ttl_raw.bin`, but it does not write raw TTL payload bytes
into parquet outputs.

Instead, it stores encoded derivatives:

- `ttl_edges.parquet`
- `ttl_pulses.parquet`

Those encoded tables are sufficient for most downstream analysis. The raw binary can
remain as an archive artifact for re-decoding if needed.
