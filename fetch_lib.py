"""Extract libprinics_ip.so (the KODAK Photo Printer app's color library) into lib/.

The library is proprietary and not included in this repo. Get the app's arm64 APK yourself, then run this
script on it. Any of these work: the .xapk bundle, the config.arm64_v8a.apk split, a single universal .apk,
or a directory containing them. Tested with app v5.1.3 (com.prinics.kodak.photoprinter, version code 4900064).

usage: python fetch_lib.py PATH
"""
import hashlib
import io
import sys
import zipfile
from pathlib import Path

MEMBER = 'lib/arm64-v8a/libprinics_ip.so'
SHA256 = '0a249510c0a2b3dcc809c2b8ca74c2788b2f294197ea591296b0b0e13e1689e2'  # v5.1.3
DEST = Path(__file__).resolve().parent / 'lib/libprinics_ip.so'


def search(zf):
    if MEMBER in zf.namelist():
        return zf.read(MEMBER)
    for name in zf.namelist():
        if name.endswith('.apk'):
            if (data := search(zipfile.ZipFile(io.BytesIO(zf.read(name))))) is not None:
                return data
    return None


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    src = Path(sys.argv[1])
    paths = sorted(p for p in src.iterdir() if p.suffix in ('.apk', '.xapk')) if src.is_dir() else [src]
    for p in paths:
        if (data := search(zipfile.ZipFile(p))) is not None:
            break
    else:
        raise SystemExit(f'{MEMBER} not found in {src}')
    DEST.parent.mkdir(exist_ok=True)
    DEST.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    print(f'wrote {DEST} ({len(data)} bytes)')
    if digest != SHA256:
        print(f'warning: sha256 {digest} differs from the tested v5.1.3 build; color output may differ or fail')


if __name__ == '__main__':
    main()
