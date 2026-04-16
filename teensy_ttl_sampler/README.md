# teensy_ttl_sampler

Teensy 4.1 firmware for sampling 4 TTL inputs at 20 kHz and streaming framed binary
samples over USB CDC.

## What This Firmware Sends

After USB serial becomes active, Teensy sends one handshake line:

```text
#TTL_HANDSHAKE {"version":1,"sampling_rate_hz":20000,"frame_size":2048,"channel_map":[1,2,3,4],"firmware_version":"...","git_hash":"..."}
```

Then it streams binary frames repeatedly:

- `magic`: `0xAA 0x55` (2 bytes)
- `frame_id`: `uint32` little-endian
- `n_samples`: `uint16` little-endian
- `t_us_first_sample`: `uint64` little-endian
- `payload[n_samples]`: one byte per sample, 4 LSB bits are channels 0..3

## Pin Assignments

Current mapping in `src/main.cpp`:

- Channel 0 -> pin 2
- Channel 1 -> pin 3
- Channel 2 -> pin 4
- Channel 3 -> pin 5

## Install Options

### Option A (Recommended): VS Code + PlatformIO extension

1. Install [Visual Studio Code](https://code.visualstudio.com/).
2. Open VS Code Extensions and install **PlatformIO IDE**.
3. Open this folder: `teensy_ttl_sampler/`.
4. Connect Teensy 4.1 by USB.
5. Build: PlatformIO "Build" button (checkmark) or terminal `pio run`.
6. Upload: PlatformIO "Upload" button (right arrow) or `pio run -t upload`.

### Option B: No IDE (CLI only)

1. Install Python 3.
2. Install PlatformIO CLI:
   ```bash
   pip install platformio
   ```
3. Open terminal in `teensy_ttl_sampler/`.
4. Build:
   ```bash
   ./build.sh
   ```
5. Upload:
   ```bash
   ./upload.sh
   ```

Windows PowerShell equivalents:

```powershell
.\build.ps1
.\upload.ps1
```

Linux note:

- This repo is currently configured to use `upload_protocol = teensy-cli` in
  [platformio.ini](platformio.ini), which is more reliable than the GUI loader
  for native Linux bring-up.

## First Upload Notes

- If upload does not start automatically, press the **Program** button on Teensy once.
- Confirm Windows sees the device (Device Manager).
- Use a data USB cable (not power-only).

### Native Linux Upload Notes

If build succeeds but upload fails with `Found device but unable to open`, Linux
usually sees the Teensy bootloader but lacks the needed `udev` permissions.

Install PJRC's Teensy rule:

```bash
curl -fsSL https://www.pjrc.com/teensy/00-teensy.rules | sudo tee /etc/udev/rules.d/00-teensy.rules >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug/replug the Teensy and press the physical **Program** button once
before retrying upload.

Useful checks:

```bash
lsusb
ls -l /etc/udev/rules.d/00-teensy.rules
```

Typical bootloader detection on Linux appears as:

- `16c0:0478 Van Ooijen Technische Informatica Teensy Halfkay Bootloader`

## Runtime Notes

- Sampling remains fixed at 20 kHz.
- USB writes are non-blocking with `availableForWrite()`: if host is too slow, frame output can be dropped instead of stalling sampling.
- `frame_id` increments every produced frame so host software can detect dropped frames.
- Samples are raw GPIO reads, not inverted logic. `1` means Teensy pin HIGH and
  `0` means Teensy pin LOW.

## Verifying Quickly

After flashing, use the Python debug script from repo root:

```bash
uv run --python .venv/bin/python scripts/ttl_record.py --port COM7 --duration 10
```

Expected result: generated `ttl_raw.bin` and `ttl_meta.json` in a timestamped folder.

For human-readable live bring-up on Linux:

```bash
uv run --python .venv/bin/python scripts/ttl_monitor.py --port /dev/ttyACM0
```

This is preferable to a plain serial monitor because the firmware sends binary
frames after the handshake line.

## One-Channel Bring-Up

Validated bench setup for channel 0 only:

- `CH0` is Teensy `pin 2`
- direct test: `pin 2 -> 3.3V` should read `CH0=1`
- direct test: `pin 2 -> GND` should read `CH0=0`
- perfboard output-side wiring for one populated H11L1 channel:
  - perfboard `VO` -> Teensy `pin 2`
  - perfboard output-side `3.3V` -> Teensy `3.3V`
  - perfboard output-side `GND` -> Teensy `GND`

With the current H11L1 output stage:

- idle/unasserted should read HIGH on the Teensy input (`CH0=1`)
- asserted TTL should pull `VO` LOW and read as `CH0=0`

Because firmware currently uses `INPUT` rather than `INPUT_PULLUP`, a bare
disconnected Teensy pin is not a valid low-state test. Use direct `3.3V`/`GND`
jumper tests or a fully powered perfboard output stage.
