---
name: kodak-print
description: Process photos and print them on the user's KODAK Mini 2 Retro (P210R) instant printer from this Mac over Bluetooth LE. Use when the user asks to print a photo/picture/image on the Kodak, the mini printer, or the instant printer.
---

# Print photos on the KODAK Mini 2 Retro

Tooling lives in `~/dev/kodak-p210r-print` (details and history: `HANDOFF.md` there). Run every command from that directory.

```
cd ~/dev/kodak-p210r-print
UV="uv run -q --with bleak --with pillow --with unicorn --with pyelftools python -I -u"
```

## Printer facts
- Shows up over BLE as `Kodak Instant-XXXX`. No pairing; it's not visible in macOS Bluetooth settings. It must be powered on (white light) and not connected to a phone.
- It auto-powers off when idle. If the script says "No BLE device ... found", ask the user to power it on, then retry.
- Print size is 640x1024 portrait (2:3.2). `kodak_ble.py` rotates landscape images 90° and **center-crops** to fill.
- One print takes about 72-80 s and uses one sheet. Temperature rises 12-17 per print (e.g. 32 → 49). The script checks only before sending and waits if it's 55 or more.
- Cooldown (measured): exponential, τ ≈ 5 min, toward an idle temp of about 33. From 49: 43 at ~2 min, 40 at ~4, 37 at ~7, 35 at ~10, 33 at ~20. Read the temp without printing: `$UV kodak_ble.py ready/ANY.jpg --dry-run`.
- Don't touch the USB-C serial port (`/dev/cu.usbmodem*`). It's a factory provisioning console, not a print path.

## Workflow: staged/ → compose → ready/ → print
**Compose is required.** Never print a file from `staged/`, `~/Downloads` or anywhere else outside `ready/`. Even "print X" or `/kodak-print X` means stage → compose → print. Skip compose only if the user explicitly says to skip cropping or to use center-crop.

1. **Stage.** Copy source images into `~/dev/kodak-p210r-print/staged/`. JPEG, PNG, HEIC, WebP and TIFF all work.
2. **Compose.** The user does this interactively. Start the tool in the background and tell the user the URL, then **stop and wait** until the user says they're done. Don't print in the meantime.
   `uv run -q --with pillow --with pillow-heif python -I compose.py`
   It serves `http://127.0.0.1:8765/` and opens the browser. Controls:
   - drag to pan, mouse wheel to zoom
   - R rotate 90°, O switch portrait/landscape
   - C fill, F fit (adds a border)
   - B border white/black, G thirds grid
   - S or Enter save and go to the next photo, ←/→ previous/next
   Saving writes `ready/STEM.jpg` at the exact print size (640x1024 or 1024x640). Crop settings go in `ready/crops.json`, so reopening a photo restores them. Stop the server when the user is done.
3. **Review.** Check that `ready/STEM.jpg` exists for each photo, then read it to sanity-check it.
4. **Confirm before printing.** Printing uses film. State the `ready/` file(s), copies and color mode, then get a go-ahead. Asking for a print at the start doesn't count as a go-ahead for the composed result.
5. **Print**, one photo per command, in sequence. Never run two at once.
   `$UV kodak_ble.py ready/NAME.jpg [--copies N] 2>&1 | tr '\r' '\n'`
   Ready files are already at print size, so the script doesn't crop them.
   Success is `printed=1/1 ... done`. Then go to the next photo.
6. **Report** concisely: what printed, plus any failure with its status line.

## Color modes (`--color`)
- `lut` (default, best): the app's color tables, emulated. Slight green tint in dark grays.
- `raw`: no correction. Slightly pink overall.
- `full`: don't use. The app's thermal step bands mid-grays.
- Pending idea: if the user notices a green tint in shadows on real photos, blend some `raw` back into the shadows for that photo. Try it per photo only on request.

## Troubleshooting
- Stuck at `printed=0/1` with `busy=0` and no motion: data was dropped. Have the user power-cycle the printer and retry; `--delay 0.03` slows the packets.
- `busy=1` before sending: the printer is still finishing a job. Wait and retry.
- Status bytes: [1] busy, [2] phase (ff, 0-3, 7 = done), [6:8] temp, [10]/[11] printed/total.
