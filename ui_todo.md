UI TODO

- Replace free-text confirmation prompts like `Type yes to verify and connect the selected laser` with a real yes/no chooser.
- Invalid or stray input should not fail the whole setup step. Re-prompt with explicit Yes / No options instead.
- Treat empty input or junk input defensively, for example:
  - show `Please choose Yes or No`
  - return to the same confirmation prompt
  - do not raise `Invalid boolean value`
- Preflight should distinguish clearly between:
  - configured preferred Doric port from YAML
  - currently selected/verified live Doric port from `Setup Laser`
- Avoid summaries that show `port=0` in a misleading way when the run is still waiting for `Setup Laser` to resolve and confirm a real port.
- If a preferred port exists in config, surface that as `preferred_port=...` rather than implying no port is known.
- Open-loop runtime status is still too ambiguous.
- `state=scheduled` vs `state=running` is not operator-friendly enough; use explicit human wording like `Waiting for start delay` vs `Laser Active`.
- The open-loop panel currently shows configured `run_for_minutes` in a way that looks like live remaining time.
- Split configured duration from live status:
  - `Configured Run Window`
  - `Remaining`
  - `Started At`
  - `Ends At`
- Compact strings like `open_loop ch3,ch4 1.0` are too encoded for live use; spell out `Open-loop on ch3,ch4 for 1.0 min`.
- Highest-friction spots observed so far:
  - laser setup confirmation
  - per-channel laser verification confirmation
  - active laser output test confirmation
  - launch re-verification confirmation
- Goal: better handholding and fewer accidental failures from mistyped input or random keyboard noise.

Verbatim UX feedback to preserve for future session:

More here...


╭─────────────────────────── Safety Warnings ───────────────────────────╮
│ - LASER RUN: real hardware setup is required before launch            │
│ - OPEN LOOP LASER: output can begin on schedule after setup completes │
╰───────────────────────────────────────────────────────────────────────╯
Safety behavior: if stimulation hardware fails to initialize, outputs remain OFF for this run.

╭──────────────────────────────────────── Verify Experiment Setup ─────────────────────────────────────────╮
│ TCP config: [CONFIGURED] 2 configured device(s)                                                          │
│ TCP connectivity: [OK] connected=2 unreachable=0                                                         │
│ Laser controller state: [OK] enabled=True mode=laser control_mode=open_loop                              │
│ Setup Laser required before launch: select device, verify USB mode, confirm current and channel mapping. │
│ Selected laser: Port 194 | open=2 close=2                                                                │
│ Teensy TTL ingest: [OK] enabled=True port=COM3                                                           │
│ Setup Teensy required before launch: select serial device and validate handshake.                        │
│ Selected Teensy: COM3 | USB Serial Device (COM3) | USB VID:PID=16C0:0483 SER=19300030                    │
│ Launch plan: mode=open_loop target_channels=['ch1', 'ch2'] run_for_minutes=1.0 start_delay_seconds=30.0  │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭──────────────────────────────────────────────────────────────────────────────╮
│ Launch Contract                                                              │
│ run_type=open_loop  stim_mode=laser  output_behavior=ACTIVE OUTPUTS POSSIBLE │
│ resolved_channels=ch1:1, ch2:2                                               │
│ pulse_hz=1.0  pulse_period_ms=1000.0  pulse_time_on_ms=10.0                  │
│ train_on_seconds=60.0  train_off_seconds=0.0                                 │
│ ttl_capture=enabled=True port=COM3                                           │
│ run_window=minutes=1.0 start_delay_seconds=30.0                              │
╰──────────────────────────────────────────────────────────────────────────────╯
Launch experiment now? (yes/no) yes

Not convinced this is useful for a human and that a human understands that the delay will start after pressing enter

A ton of info, which I understand why and requested, unsure if the info presentation or type or quality or what....we can revisit, but keep the pasted content verbatim so we can recover on future session
