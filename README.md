# kodak-p210r-print

Print from a Mac (or Linux) to a **KODAK Mini 2 Retro (P210R)** instant printer over Bluetooth LE, without the phone app.

- `kodak_ble.py`: sends a photo to the printer over BLE.
- `compose.py`: minimal browser-based crop/compose tool that writes print-ready 640x1024 images.
- `prinics_ip.py`: runs the official app's color-correction tables (native arm64 library) under the Unicorn emulator.
- `fetch_lib.py`: extracts that library from the app APK. It is proprietary and not included here.

Requires [uv](https://docs.astral.sh/uv/). Dependencies are pulled in per command with `uv run --with ...`.

## Setup

The printer advertises over BLE as `Kodak Instant-XXXX`. You don't pair it, and it won't appear in macOS Bluetooth settings. Power it on and make sure no phone is connected to it. On macOS, the terminal app needs Bluetooth permission the first time.

Color correction (recommended) needs the app's library:

1. Get the arm64 APK of **KODAK Photo Printer** (`com.prinics.kodak.photoprinter`; tested with v5.1.3). For example, pull it from an Android device with `adb shell pm path com.prinics.kodak.photoprinter` and `adb pull`.
2. Run `python3 fetch_lib.py PATH`. PATH can be the `.xapk`, the `config.arm64_v8a.apk` split, a universal `.apk`, or a directory containing them. The library is written to `lib/libprinics_ip.so`.

Without the library, use `--color raw`.

## Usage

Stage, compose, print:

```sh
# 1. put photos in staged/ (JPEG, PNG, HEIC, WebP, TIFF)
# 2. compose: opens http://127.0.0.1:8765/
uv run -q --with pillow --with pillow-heif python -I compose.py
# 3. print
uv run -q --with bleak --with pillow --with unicorn --with pyelftools python -I -u kodak_ble.py ready/photo.jpg
```

### Compose controls

| Key or action | Effect |
| --- | --- |
| drag / mouse wheel | pan / zoom |
| R | rotate 90° |
| O | switch portrait/landscape |
| C / F | fill / fit (adds a border) |
| B | border white/black |
| G | thirds grid |
| S or Enter | save and go to next |
| ← / → | previous / next |

Saved images go to `ready/` at the exact print size. Crop settings are kept in `ready/crops.json`.

### Print options

`kodak_ble.py IMAGE [--copies N] [--color lut|raw|full] [--dry-run] [--save out.jpg] [--delay 0.02]`

If the image isn't already 640x1024, landscape images are rotated 90° and then center-cropped to fill.

- `--color lut` (default): the app's 3D and 1D color tables. Most accurate; dark grays get a slight green tint.
- `--color raw`: no correction. Prints slightly pink.
- `--color full`: the app's entire pipeline, including a thermal-compensation step. That step bands mid-grays, so this mode isn't recommended.

## Protocol

Reverse-engineered from the Android app, v5.1.3.

- GATT service `9877e0cb-a4d5-4860-a50b-ac10d83b6967`
  - `9877e0cc`: TX, write without response
  - `9877e0cd`: 16-byte status, read
- Start packet: `[len:3 BE][00 00][00][copies][00 00 00]`
- Data packets: `[offset:3 BE][n:2 BE][n bytes]`, n = 200
- Packets must be paced about 20 ms apart. If they aren't, the printer drops them and the job stalls (blue light, no print).
- Payload: JPEG at 640x1024, zero-padded to at least 33792 bytes.
- Status bytes:
  - `[1]` busy
  - `[2]` phase: `ff`, then `0`–`3`, then `7` = done
  - `[6:8]` temperature. The app waits if it's ≥ 55.
  - `[10]/[11]` printed/total
- A print takes about 72 s after the data is sent.

The printer's USB-C port is a factory provisioning console (ESP32-S3 USB-Serial-JTAG), not a print path. Don't write to it.

## Disclaimer

This is an unofficial project, not affiliated with Kodak or Prinics. Use at your own risk.
