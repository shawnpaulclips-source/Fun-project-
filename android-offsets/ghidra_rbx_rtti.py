# Dump RBX::* vtable RVAs out of a stripped libroblox.so (ARM64, Itanium ABI).
#@category Roblox
#@keybinding
#@menupath
#@toolbar
#
# GUI:      Script Manager -> run on the open libroblox.so
# Headless: analyzeHeadless <proj> <name> -process libroblox.so \
#               -postScript ghidra_rbx_rtti.py /path/out.json
#
# Two routes. If Ghidra's GCC RTTI analyzer already labelled things, we just
# read the symbol table. Otherwise we do the Itanium walk by hand:
#
#   "N3RBX9DataModelE"      mangled type name string
#     <- type_info + 8      a pointer to that string
#     <- vtable[1]          a pointer to that type_info
#     vptr = vtable[1] + 8  what objects actually store at +0

import json
import re

from jarray import zeros
from java.nio import ByteBuffer, ByteOrder

PROG  = currentProgram
MEM   = PROG.getMemory()
BASE  = PROG.getImageBase().getOffset()
ARGS  = getScriptArgs()

NAME_RE = re.compile(r'^N3RBX[0-9]')
FLAG_RE = re.compile(r'^(D|S|DS|SD)?(FFlag|FInt|FString|FLog)[A-Z][A-Za-z0-9_]{2,}$')


def rva(addr):
    return addr - BASE


def blocks(initialized_only=True):
    out = []
    for b in MEM.getBlocks():
        if initialized_only and not b.isInitialized():
            continue
        if b.getSize() <= 0:
            continue
        out.append(b)
    return out


def read_block(b):
    n = int(b.getSize())
    if n > 400 * 1024 * 1024:
        return None
    buf = zeros(n, 'b')
    try:
        MEM.getBytes(b.getStart(), buf)
    except:
        return None
    return buf


MASK = 0xFFFFFFFFFFFFFFFF


def words(buf, start_addr, cb):
    """Call cb(value, address) for every aligned 8-byte word."""
    bb = ByteBuffer.wrap(buf)
    bb.order(ByteOrder.LITTLE_ENDIAN)
    n = len(buf) - (len(buf) % 8)
    i = 0
    while i < n:
        v = bb.getLong(i) & MASK
        if v != 0:
            cb(v, start_addr + i)
        i += 8


def cstrings(buf, start_addr, pred):
    """Yield (addr, string) for NUL-terminated printable runs matching pred."""
    out = []
    n = len(buf)
    i = 0
    cur = []
    cur_start = 0
    while i < n:
        c = buf[i] & 0xFF
        if 0x20 <= c <= 0x7E:
            if not cur:
                cur_start = i
            cur.append(chr(c))
        else:
            if c == 0 and len(cur) >= 4:
                s = ''.join(cur)
                if pred(s):
                    out.append((start_addr + cur_start, s))
            cur = []
        i += 1
    return out


def from_symbols():
    """If the GCC RTTI analyzer ran, vftables are already labelled."""
    found = {}
    st = PROG.getSymbolTable()
    it = st.getAllSymbols(True)
    while it.hasNext():
        s = it.next()
        nm = s.getName()
        if 'vftable' not in nm and '_ZTV' not in nm:
            continue
        full = s.getName(True)
        if 'RBX' not in full:
            continue
        cls = full.replace('::vftable', '').replace('_ZTV', '')
        # a vptr points past offset_to_top + typeinfo
        found[cls] = s.getAddress().getOffset() + 16
    return found


def manual():
    print('[*] manual Itanium RTTI walk')

    bl = blocks()
    cache = []
    for b in bl:
        buf = read_block(b)
        if buf is None:
            continue
        cache.append((b, buf, b.getStart().getOffset()))
        print('    block %-20s %s  %d bytes' % (b.getName(), b.getStart(), b.getSize()))

    # pass 0: mangled RBX type names + FFlag name strings
    names = {}
    flags = []
    for (b, buf, start) in cache:
        for (a, s) in cstrings(buf, start, lambda x: NAME_RE.match(x) and x.endswith('E')):
            names[a] = s
        for (a, s) in cstrings(buf, start, lambda x: FLAG_RE.match(x) is not None):
            flags.append((a, s))
    print('    RBX type names: %d' % len(names))
    print('    flag-name strings: %d' % len(flags))
    if not names:
        return {}, flags

    # pass 1: type_info objects
    ti = {}

    def pass1(v, a):
        nm = names.get(v)
        if nm is not None and (a % 8) == 0:
            ti.setdefault(a - 8, nm)

    for (b, buf, start) in cache:
        words(buf, start, pass1)
    print('    type_info objects: %d' % len(ti))

    # pass 2: vtables
    out = {}

    def pass2(v, a):
        nm = ti.get(v)
        if nm is None or (a % 8) != 0:
            return
        out.setdefault(nm, a + 8)

    for (b, buf, start) in cache:
        words(buf, start, pass2)
    print('    vtables: %d' % len(out))
    return out, flags


def demangle(n):
    # N3RBX9DataModelE -> RBX::DataModel ; nested names keep their segments
    m = n
    if not m.startswith('N') or not m.endswith('E'):
        return n
    m = m[1:-1]
    parts = []
    i = 0
    while i < len(m):
        j = i
        while j < len(m) and m[j].isdigit():
            j += 1
        if j == i:
            break
        ln = int(m[i:j])
        parts.append(m[j:j + ln])
        i = j + ln
    return '::'.join(parts) if parts else n


def main():
    print('[*] %s  image base %s  size %d' % (PROG.getName(), hex(BASE), PROG.getMemory().getSize()))

    vt = from_symbols()
    flags = []
    if vt:
        print('[*] took %d vftables from the symbol table' % len(vt))
    else:
        vt, flags = manual()

    if not vt:
        print('[!] no RBX vtables. either the RTTI is gone or the data blocks are not loaded.')
        return

    result = {
        'imageBase': BASE,
        'vtableRVA': {},
        'flagStringRVA': {},
    }

    print('')
    print('[*] vtables (class = RVA)')
    for mangled in sorted(vt.keys()):
        pretty = demangle(mangled)
        r = rva(vt[mangled])
        result['vtableRVA'][pretty] = r
        print('    %-52s 0x%X' % (pretty, r))

    for (a, s) in flags:
        result['flagStringRVA'][s] = rva(a)

    if flags:
        print('')
        print('[*] %d flag-name strings recorded. xref each one to reach its global;' % len(flags))
        print('    the registration writes the flag object into a static, and that')
        print('    static is the address you want for a flag toggle.')

    if ARGS and len(ARGS) > 0:
        path = ARGS[0]
        f = open(path, 'w')
        f.write(json.dumps(result, indent=2, sort_keys=True))
        f.close()
        print('')
        print('[*] wrote %s' % path)
    else:
        print('')
        print('[*] pass an output path as a script arg to get JSON on disk')


main()
