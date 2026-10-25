"""Print to a KODAK Mini 2 Retro (P210R) over BLE (macOS/Linux).

Protocol from com.prinics.kodak.photoprinter v5.1.3 (p003a3/f.java, PrintService.java):
- Service 9877e0cb-..., char 0 (9877e0cc) write-without-response, char 1 (9877e0cd) status read.
- Start job: [len:3][00 00][cmd=0][copies][00 00 00]
- Data:      [offset:3][n:2][n bytes], n=200
- Payload: JPEG 640x1024 q90, zero-padded to >= 33792 bytes.
- Status (16 B): [1] busy, [2] phase (ff, 0-3 = color passes, 7 = done), [3] state, [6:8] temperature (app waits if >= 55),
  [10]/[11] printed/total, [14] cartridge type.

usage: uv run --with bleak --with pillow --with unicorn --with pyelftools python kodak_ble.py photo.jpg [--copies N] [--color raw|lut|full] [--dry-run]
"""
import argparse
import asyncio
import io
import sys
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner
from PIL import Image, ImageOps

SVC = '9877e0cb-a4d5-4860-a50b-ac10d83b6967'
TX = '9877e0cc-a4d5-4860-a50b-ac10d83b6967'
RX = '9877e0cd-a4d5-4860-a50b-ac10d83b6967'
SIZE = (640, 1024)
MIN_LEN = 33792
CHUNK = 200


def prepare(src, color='lut'):
    im = ImageOps.exif_transpose(Image.open(src)).convert('RGB')
    if im.width > im.height:
        im = im.rotate(90, expand=True)
    sys.path.insert(0, str(Path(__file__).parent))
    if color == 'full':  # app's whole native pipeline, emulated; thermal step causes banding
        from prinics_ip import process
        im = process(im)
    else:
        im = ImageOps.fit(im, SIZE, Image.LANCZOS)
        if color == 'lut':  # app's color tables only
            from prinics_ip import apply_luts
            im = apply_luts(im)
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=90)
    data = buf.getvalue()
    return data + bytes(max(0, MIN_LEN - len(data)))


def fmt(s):
    return f'{s.hex(" ")}  busy={s[1]} phase={s[2]} state={s[3]} temp={(s[6] << 8) | s[7]} printed={s[10]}/{s[11]}'


async def find(name):
    dev = await BleakScanner.find_device_by_filter(lambda d, a: (d.name or a.local_name or '').startswith(name), timeout=15)
    if not dev:
        raise SystemExit(f'No BLE device named {name}* found. Printer on and not connected to a phone?')
    return dev


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--copies', type=int, default=1)
    ap.add_argument('--name', default='Kodak Instant')
    ap.add_argument('--timeout', type=float, default=180)
    ap.add_argument('--delay', type=float, default=0.02, help='seconds between data packets')
    ap.add_argument('--color', choices=['raw', 'lut', 'full'], default='lut', help='raw: none; lut: app color tables; full: app pipeline incl. thermal')
    ap.add_argument('--save', help='also write the processed JPEG here')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    payload = prepare(a.image, a.color)
    if a.save:
        Path(a.save).write_bytes(payload)
    dev = await find(a.name)
    print('device:', dev.name, dev.address, '| payload', len(payload), 'bytes')
    async with BleakClient(dev, timeout=20) as c:
        status = bytes(await c.read_gatt_char(RX))
        print('status:', fmt(status))
        if status[1] != 0:
            raise SystemExit('Printer busy; not sending.')
        while (status[6] << 8 | status[7]) >= 55:
            print('too hot, waiting'); await asyncio.sleep(1)
            status = bytes(await c.read_gatt_char(RX))
        if a.dry_run:
            return

        n = len(payload)
        await c.write_gatt_char(TX, n.to_bytes(3, 'big') + b'\0\0' + bytes([0, a.copies, 0, 0, 0]), response=False)
        await asyncio.sleep(0.2)
        t0 = time.time()
        for off in range(0, n, CHUNK):
            part = payload[off:off + CHUNK]
            await c.write_gatt_char(TX, off.to_bytes(3, 'big') + len(part).to_bytes(2, 'big') + part, response=False)
            await asyncio.sleep(a.delay)
            if off % (CHUNK * 50) == 0:
                print(f'\rsent {off + len(part)}/{n}', end='', flush=True)
        print(f'\rsent {n}/{n} in {time.time() - t0:.1f}s')

        last, seen_busy, end = None, False, time.time() + a.timeout
        while time.time() < end:
            s = bytes(await c.read_gatt_char(RX))
            if s != last:
                print(f'{time.time() - t0:6.1f}s', fmt(s)); last = s
            seen_busy |= s[1] == 1
            if seen_busy and s[1] == 0 and s[11] and s[10] >= s[11]:
                print('done'); return
            await asyncio.sleep(0.6)


if __name__ == '__main__':
    asyncio.run(main())
