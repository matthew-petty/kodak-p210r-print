"""Run the Kodak app's native image pipeline (libprinics_ip.so, arm64 Android) under Unicorn.

Replicates Prepare.prepare() for the Mini 2 Retro (printer type %10 == 2):
globals rw=640, rh=1024, model=2, then
process_image(rgb, w, h, &rw, &rh, border=0, 0, 0, 640, 1024, chip=1, model=2)
= resize_and_crop -> sharpen -> 3D LUT -> 1D LUT -> thermal (THC) compensation.

usage: uv run --with unicorn --with pyelftools --with pillow python prinics_ip.py in.jpg out.jpg
"""
import math
import struct
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from PIL import Image, ImageOps
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *

LIB = Path(__file__).parent / 'lib/libprinics_ip.so'
BASE, STUBS, HEAP, STACK, TLS, END = 0x10000000, 0x20000000, 0x30000000, 0x70000000, 0x71000000, 0x72000000
HEAP_SIZE, STACK_SIZE = 0x20000000, 0x800000
RET = 0xd65f03c0


class Emu:
    def __init__(self):
        if not LIB.exists():
            raise SystemExit(f'{LIB} missing: run fetch_lib.py on the app APK (see README), or use --color raw')
        self.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)  # enable FP/SIMD
        elf = ELFFile(open(LIB, 'rb'))
        data = LIB.read_bytes()
        top = max(s['p_vaddr'] + s['p_memsz'] for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD')
        uc.mem_map(BASE, (top + 0xfff) & ~0xfff)
        for s in elf.iter_segments():
            if s['p_type'] == 'PT_LOAD':
                uc.mem_write(BASE + s['p_vaddr'], data[s['p_offset']:s['p_offset'] + s['p_filesz']])
        dynsym = elf.get_section_by_name('.dynsym')
        self.sym = {s.name: s['st_value'] for s in dynsym.iter_symbols() if s['st_value']}
        self.stubs, self.stub_names = {}, {}
        uc.mem_map(STUBS, 0x10000)
        for name in ('.rela.dyn', '.rela.plt'):
            for r in elf.get_section_by_name(name).iter_relocations():
                t, off, add = r['r_info_type'], r['r_offset'], r['r_addend']
                if t == 1027:  # RELATIVE
                    val = BASE + add
                else:
                    s = dynsym.get_symbol(r['r_info_sym'])
                    val = (BASE + s['st_value'] if s['st_value'] else self.stub(s.name)) + add
                uc.mem_write(BASE + off, struct.pack('<Q', val))
        uc.hook_add(UC_HOOK_CODE, self.on_stub, begin=STUBS, end=STUBS + 0xffff)
        uc.mem_map(HEAP, HEAP_SIZE)
        self.brk, self.sizes = HEAP, {}
        uc.mem_map(STACK, STACK_SIZE)
        uc.mem_map(TLS, 0x1000)
        uc.mem_write(TLS + 0x28, struct.pack('<Q', 0x5ca1ab1e))
        uc.reg_write(UC_ARM64_REG_TPIDR_EL0, TLS)
        uc.mem_map(END, 0x1000)
        uc.mem_write(END, struct.pack('<I', RET))

    def stub(self, name):
        if name not in self.stubs:
            addr = STUBS + 4 * len(self.stubs)
            self.uc.mem_write(addr, struct.pack('<I', RET))
            self.stubs[name], self.stub_names[addr] = addr, name
        return self.stubs[name]

    def alloc(self, n):
        n = max(int(n), 1)
        p = self.brk
        self.brk = (p + n + 15) & ~15
        if self.brk > HEAP + HEAP_SIZE:
            raise MemoryError('emulated heap exhausted')
        self.sizes[p] = n
        return p

    def on_stub(self, uc, addr, size, _):
        name = self.stub_names[addr]
        x = lambda i: uc.reg_read(UC_ARM64_REG_X0 + i)
        s = lambda i: struct.unpack('<f', uc.reg_read(UC_ARM64_REG_Q0 + i).to_bytes(16, 'little')[:4])[0]
        d = lambda i: struct.unpack('<d', uc.reg_read(UC_ARM64_REG_Q0 + i).to_bytes(16, 'little')[:8])[0]
        ret = lambda v: uc.reg_write(UC_ARM64_REG_X0, v & (2**64 - 1))
        retf = lambda v: uc.reg_write(UC_ARM64_REG_Q0, int.from_bytes(struct.pack('<f', v), 'little'))
        retd = lambda v: uc.reg_write(UC_ARM64_REG_Q0, int.from_bytes(struct.pack('<d', v), 'little'))
        if name == 'malloc':
            ret(self.alloc(x(0)))
        elif name == 'calloc':
            n = x(0) * x(1); p = self.alloc(n); uc.mem_write(p, bytes(n)); ret(p)
        elif name == 'realloc':
            old, n = x(0), x(1); p = self.alloc(n)
            if old:
                uc.mem_write(p, bytes(uc.mem_read(old, min(self.sizes.get(old, n), n))))
            ret(p)
        elif name == 'posix_memalign':
            p = self.alloc(x(2) + 64); p = (p + 63) & ~63
            uc.mem_write(x(0), struct.pack('<Q', p)); ret(0)
        elif name in ('free', 'printf', '__android_log_print', 'fclose', 'fflush'):
            ret(0)
        elif name in ('memset', '__memset_chk'):
            uc.mem_write(x(0), bytes([x(1) & 255]) * x(2)); ret(x(0))
        elif name in ('memcpy', 'memmove', '__memcpy_chk', '__memmove_chk'):
            if x(2):
                uc.mem_write(x(0), bytes(uc.mem_read(x(1), x(2))))
            ret(x(0))
        elif name == 'fopen':
            ret(0)
        elif name in ('snprintf', 'vsnprintf', '__vsnprintf_chk', 'sprintf', '__vsprintf_chk'):
            uc.mem_write(x(0), b'\0'); ret(0)  # only used for debug-dump filenames
        elif name == 'abs':
            v = x(0) & 0xffffffff; v = v - 2**32 if v >= 2**31 else v; ret(abs(v))
        elif name == 'powf':
            try:
                retf(math.pow(s(0), s(1)))
            except (ValueError, OverflowError):
                retf(float('nan'))
        elif name == 'expf':
            try:
                retf(math.exp(s(0)))
            except OverflowError:
                retf(float('inf'))
        elif name == 'exp':
            try:
                retd(math.exp(d(0)))
            except OverflowError:
                retd(float('inf'))
        else:
            raise RuntimeError(f'unimplemented import called: {name}')

    def call(self, name, args):
        uc = self.uc
        regs, stack = args[:8], args[8:]
        sp = STACK + STACK_SIZE - 0x1000
        for i, v in enumerate(stack):
            uc.mem_write(sp + 8 * i, struct.pack('<Q', v & (2**64 - 1)))
        for i, v in enumerate(regs):
            uc.reg_write(UC_ARM64_REG_X0 + i, v & (2**64 - 1))
        uc.reg_write(UC_ARM64_REG_SP, sp)
        uc.reg_write(UC_ARM64_REG_LR, END)
        uc.emu_start(BASE + self.sym[name], END)
        return uc.reg_read(UC_ARM64_REG_X0)

    def set_int(self, name, v):
        self.uc.mem_write(BASE + self.sym[name], struct.pack('<i', v))

    def get_int(self, ptr):
        return struct.unpack('<i', self.uc.mem_read(ptr, 4))[0]


def process(im, chip=1, model=2):
    """PIL RGB image -> processed PIL RGB image (640x1024)."""
    e = Emu()
    e.set_int('rw', 640); e.set_int('rh', 1024); e.set_int('model', model)
    w, h = im.size
    src = e.alloc(w * h * 3)
    e.uc.mem_write(src, im.tobytes())
    rw, rh = BASE + e.sym['rw'], BASE + e.sym['rh']
    out = e.call('_Z13process_imagePhiiPiS0_biiiiii', [src, w, h, rw, rh, 0, 0, 0, 640, 1024, chip, model])
    if not out:
        raise RuntimeError('process_image returned NULL')
    W, H = e.get_int(rw), e.get_int(rh)
    return Image.frombytes('RGB', (W, H), bytes(e.uc.mem_read(out, W * H * 3)))


def apply_luts(im, chip=1, model=2):
    """Color tables only (3D LUT + 1D LUT), no resize/rotate/sharpen/thermal step."""
    e = Emu()
    e.set_int('rw', 640); e.set_int('rh', 1024); e.set_int('model', model)
    w, h = im.size
    p = e.alloc(w * h * 3)
    e.uc.mem_write(p, im.tobytes())
    e.call('_Z9apply_lutPhiiii', [p, w, h, chip, model])
    e.call('_Z12apply_1d_lutPhiiii', [p, w, h, model, chip])
    return Image.frombytes('RGB', (w, h), bytes(e.uc.mem_read(p, w * h * 3)))


if __name__ == '__main__':
    im = ImageOps.exif_transpose(Image.open(sys.argv[1])).convert('RGB')
    out = process(im)
    print('output', out.size)
    out.save(sys.argv[2], 'JPEG', quality=90)
