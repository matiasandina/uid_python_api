# Real-Time UID Mouse Matrix Temperature Monitoring System

Temperature acquisition and stimulation control stack for UID Mouse Matrices with:
- TCP reader ingest
- live Rich UI
- closed-loop and open-loop stimulation modes
- optional Teensy TTL capture pipeline

## Attribution

- The TCP module builds on earlier contribution work by Derek Jordan.
- The current runtime, UI, stimulation, replay, TTL capture, and configuration system were created and are maintained by Matias Andina.

## Quick Start

```bash
uv venv -p 3.11 .venv
uv sync
cp config.example.yaml config.local.yaml
# edit config.local.yaml for machine-specific values
uv run --python .venv/bin/python main.py --config configs/open_loop/openloop_20hz.yaml
```

## Main Entrypoints

- `main.py`: live acquisition + preflight
- `replay.py`: replay CSV data through the runtime
- `simulate.py`: synthetic scenario generation

## Config

Config file roles:
- `config.local.yaml`: machine-specific values used at runtime
- `configs/...`: experiment/protocol overlays passed with `--config`
- `config.example.yaml`: documentation/template only, not runtime input

See detailed config docs:
- `docs/config.md`

## Detailed Docs

- `docs/operator_guide.md`: human-first entry point for operators
- `docs/preflight.md`: step-by-step preflight walkthrough
- `docs/open_loop.md`: operator-facing open-loop timing guide
- `docs/closed_loop.md`: operator-facing closed-loop guide
- `docs/troubleshooting.md`: symptom-first troubleshooting
- `docs/docs_site_plan.md`: docs IA and site-bootstrap plan
- `docs/runtime.md`: architecture and runtime flow
- `docs/stimulation.md`: closed/open-loop semantics and safety
- `docs/ttl_capture.md`: Teensy TTL capture setup and outputs
- `docs/analysis.md`: session analysis loaders, timezone handling, and normalized tables
- `replay_samples/README.md`: where to place local replay CSV files

## Docs Site

As of 2026-04-15, the repo includes a Great Docs scaffold for GitHub Pages:

- config: `great-docs.yml`
- user guide pages: `user_guide/`
- workflow: `.github/workflows/docs.yml`

Local docs commands:

```bash
uv sync --group docs --group dev
uv run great-docs build
```

The GitHub Actions workflow installs Quarto and publishes the site to Pages from `main`.

## Notes

- `replay_samples/` is intentionally local-data only (ignored except README).
- Hardware validation on Windows remains required before merge to `main`.

## Analysis

Install the optional analysis dependencies:

```bash
uv sync --extra analysis
```

Build normalized session tables from a run folder:

```bash
uv run python -m analysis_tools.cli data/<session_dir>
```

The analysis parquet outputs store normalized temperature, trigger, stimulation-window,
TTL-edge, and TTL-pulse tables. They do not embed raw `ttl_raw.bin` bytes.
