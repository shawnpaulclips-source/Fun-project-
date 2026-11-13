#!/usr/bin/env python3
"""
rbx_static.py — on-device static offset dump from libroblox.so.

Pure Python + pyelftools. No Ghidra, no disassembler, no running process.
Runs fine in Termux on the phone itself:

    pip install pyelftools
    python3 rbx_static.py dump/libroblox.so -o dump/static.json

Does the Itanium RTTI walk straight on the ELF section bytes:
  "N3RBX9DataModelE"   mangled type-name string in .rodata
    <- type_info+8     a word in .data.rel.ro pointing at that string
    <- vtable[1]       a word pointing at the type_info
    vptr = vtable[1]+8 what objects store at +0

For a shared object the section virtual address IS the RVA (load base 0),
so every address printed is already what you add the runtime base to.

Also scrapes every FFlag / FInt / FString / FLog name string with its RVA —
those are the xref anchors for resolving flag globals in rizin/IDA later.
"""

import argparse
import json
import re
import struct
import sys

try:
    from elftools.elf.elffile import ELFFile
except ImportError:
    sys.exit("need pyelftools:  pip install pyelftools")

NAME_RE = re.compile(rb'^N3RBX[0-9]')
FLAG_RE = re.compile(rb'^(?:D|S|DS|SD)?(?:FFlag|FInt|FString|FLog)[A-Z][A-Za-z0-9_]{2,}$')

STR_SECTIONS  = ('.rodata',)
DATA_PREFIXES = ('.data.rel.ro', '.data')


def load_sections(elf):
    out = []
    for s in elf.iter_sections():
        if s['sh_type'] == 'SHT_NOBITS':
            continue
        if s['sh_size'] == 0:
            continue
        out.append((s.name, s['sh_addr'], s.data()))
    return out


def collect(sections, names):
    picks = []
    for nm, addr, data in sections:
        if any(nm == w or nm.startswith(w) for w in names):
            picks.append((nm, addr, data))
    return picks


def scan_strings(sections):
    """Return (rbx_names, flag_names): vaddr -> bytes for matching C strings."""
    rbx, flags = {}, {}
    for _, base, data in collect(sections, STR_SECTIONS):
        start = 0
        n = len(data)
        i = 0
        while i < n:
            c = data[i]
            if c == 0:
                if i > start:
                    run = data[start:i]
                    if run.isascii() and all(0x20 <= b < 0x7f for b in run):
                        if NAME_RE.match(run) and run.endswith(b'E'):
                            rbx[base + start] = run.decode()
                        elif FLAG_RE.match(run):
                            flags[base + start] = run.decode()
                start = i + 1
            i += 1
    return rbx, flags


def iter_words(sections):
    """Yield (value, vaddr) for every 8-byte aligned word in the data sections."""
    for _, base, data in collect(sections, DATA_PREFIXES):
        if base % 8 != 0:
            # align the view
            pad = (8 - base % 8) % 8
            data = data[pad:]
            base += pad
        end = len(data) - (len(data) % 8)
        for off in range(0, end, 8):
            v = struct.unpack_from('<Q', data, off)[0]
            if v:
                yield v, base + off


def word_at(index, vaddr):
    return index.get(vaddr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('lib')
    ap.add_argument('-o', '--out')
    args = ap.parse_args()

    with open(args.lib, 'rb') as f:
        elf = ELFFile(f)
        if elf.get_machine_arch() != 'AArch64':
            print('[!] arch is %s, expected AArch64 — wrong split?' % elf.get_machine_arch())
        sections = load_sections(elf)

    print('[*] sections: ' + ', '.join('%s@0x%X(%d)' % (n, a, len(d)) for n, a, d in sections
                                        if any(n == w or n.startswith(w) for w in STR_SECTIONS + DATA_PREFIXES)))

    rbx, flags = scan_strings(sections)
    print('[*] RBX type names: %d   flag-name strings: %d' % (len(rbx), len(flags)))
    if not rbx:
        sys.exit('[!] no RBX RTTI strings — binary is stripped of type info, use the dynamic route')

    # build a full word index once (value -> list of addrs is not needed; we map addr->value
    # and separately test membership by value, so keep two views)
    words = list(iter_words(sections))
    print('[*] data words: %d' % len(words))

    # pass 1: type_info objects  (a word whose VALUE is a name address; the type_info is addr-8)
    ti = {}
    for val, addr in words:
        nm = rbx.get(val)
        if nm is not None and addr % 8 == 0:
            ti.setdefault(addr - 8, nm)
    print('[*] type_info objects: %d' % len(ti))

    # for offset_to_top check we need to read a word by address: build addr->value map
    by_addr = {addr: val for val, addr in words}

    # pass 2: vtables  (a word whose VALUE is a type_info address)
    vtables = {}
    for val, addr in words:
        nm = ti.get(val)
        if nm is None or addr % 8 != 0:
            continue
        vtbase = addr - 8                 # slot0 = offset_to_top, slot1 = type_info*
        top = by_addr.get(vtbase, None)
        if top not in (0, None):          # primary vtable has offset_to_top == 0
            if top is not None:
                continue
        vptr = addr + 8                   # what an object stores at +0
        vtables.setdefault(nm, vptr)
    print('[*] primary vtables: %d' % len(vtables))

    def demangle(n):
        if not (n.startswith('N') and n.endswith('E')):
            return n
        body = n[1:-1]
        parts, i = [], 0
        while i < len(body):
            j = i
            while j < len(body) and body[j].isdigit():
                j += 1
            if j == i:
                break
            ln = int(body[i:j])
            parts.append(body[j:j + ln])
            i = j + ln
        return '::'.join(parts) if parts else n

    result = {'imageBase': 0, 'vtableRVA': {}, 'flagStringRVA': {}}
    for mangled in sorted(vtables):
        result['vtableRVA'][demangle(mangled)] = vtables[mangled]
    for addr in sorted(flags):
        result['flagStringRVA'][flags[addr]] = addr

    print('')
    for k in sorted(result['vtableRVA']):
        print('    %-52s 0x%X' % (k, result['vtableRVA'][k]))

    js = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, 'w') as f:
            f.write(js)
        print('\n[*] wrote %s  (%d vtables, %d flag strings)' %
              (args.out, len(result['vtableRVA']), len(result['flagStringRVA'])))
    else:
        print('\n' + js)


if __name__ == '__main__':
    main()
