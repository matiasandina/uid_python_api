# Replay Samples

This folder is for local replay CSV files used with:

```bash
uv run --python .venv/bin/python replay.py --csv replay_samples/<file>.CSV --speed 100
```

Notes:
- Real experimental data must not be committed to the repository.
- Keep files local only.
- Supported schemas:
  - Legacy: `DateTime`, `UID`, `Temperature`, `Zone`
  - Raw: `Date`, `RFID`, `Temperature`, `Zone`
