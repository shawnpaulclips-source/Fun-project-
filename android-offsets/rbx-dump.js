'use strict';
/*
 * rbx-dump.js — resolve Roblox Android offsets at runtime.
 *
 *   frida -U -n com.roblox.client -l rbx-dump.js -o offsets.txt
 *   frida -U -f com.roblox.client -l rbx-dump.js --no-pause -o offsets.txt
 *
 * Stage 1  RTTI sweep: every RBX::* class -> its vtable (absolute + RVA).
 * Stage 2  heap sweep: find a live DataModel by its vptr.
 * Stage 3  probe Instance member offsets (Name, Children, Parent, ClassDescriptor).
 * Stage 4  walk the tree with the discovered offsets to prove them, emit JSON.
 *
 * Nothing here is hardcoded to a Roblox version. Re-run it after every update.
 */

var MOD = 'libroblox.so';

/* ---------------------------------------------------------------- helpers */

var PTR = Process.pointerSize;

function numOf(p) { return parseInt(p.toString(16).replace(/^0x/, ''), 16); }
function ptrOf(n) { return ptr('0x' + n.toString(16)); }

function log(s) { console.log(s); }
function hex(n) { return '0x' + n.toString(16).toUpperCase(); }

function readable(p, n) {
    try { p.readByteArray(n || 1); return true; } catch (e) { return false; }
}

function cstr(p, max) {
    max = max || 512;
    try {
        var out = '';
        for (var i = 0; i < max; i++) {
            var c = p.add(i).readU8();
            if (c === 0) break;
            if (c < 0x20 || c > 0x7e) return null;
            out += String.fromCharCode(c);
        }
        return out.length ? out : null;
    } catch (e) { return null; }
}

function printableName(s) {
    return typeof s === 'string' && s.length > 0 && s.length < 128 && /^[\x20-\x7e]+$/.test(s);
}

/*
 * libc++ std::string. Android NDK uses the default layout, but the alternate
 * one shows up in vendored builds, so probe both and take whichever yields a
 * clean string.
 *
 *   default   long: cap@0 (is_long = LSB), size@8, data@16
 *             short: size<<1 @0, chars @1
 *   alternate long: data@0, size@8, cap@16 (is_long = MSB)
 *             short: size @0, chars @1
 */
function stdStringDefault(p) {
    try {
        var first = p.readU64();
        if (first.and(1).toNumber() === 1) {
            var size = p.add(8).readU64().toNumber();
            if (size === 0 || size > 4096) return null;
            var data = p.add(16).readPointer();
            if (data.isNull() || !readable(data, size)) return null;
            return data.readUtf8String(size);
        }
        var sb = p.readU8();
        if (sb & 1) return null;
        var n = sb >> 1;
        if (n === 0 || n > 22) return null;
        return p.add(1).readUtf8String(n);
    } catch (e) { return null; }
}

function stdStringAlt(p) {
    try {
        var cap = p.add(16).readU64();
        var isLong = cap.shr(63).toNumber() === 1;
        if (isLong) {
            var size = p.add(8).readU64().toNumber();
            if (size === 0 || size > 4096) return null;
            var data = p.readPointer();
            if (data.isNull() || !readable(data, size)) return null;
            return data.readUtf8String(size);
        }
        var n = p.readU8();
        if (n === 0 || n > 22) return null;
        return p.add(1).readUtf8String(n);
    } catch (e) { return null; }
}

function stdString(p) {
    var s = stdStringDefault(p);
    if (printableName(s)) return s;
    s = stdStringAlt(p);
    if (printableName(s)) return s;
    return null;
}

/* ------------------------------------------------------------ word sweeps */

var CHUNK = 4 * 1024 * 1024;

/* Walk every 8-byte word in `ranges`, call cb(valueAsNumber, addressAsNumber). */
function sweep(ranges, cb) {
    for (var r = 0; r < ranges.length; r++) {
        var base = ranges[r].base, size = ranges[r].size;
        var done = 0;
        while (done < size) {
            var want = Math.min(CHUNK, size - done);
            want -= want % 8;
            if (want <= 0) break;
            var at = base.add(done);
            var buf = null;
            try { buf = at.readByteArray(want); } catch (e) { buf = null; }
            if (buf !== null) {
                var dv = new DataView(buf);
                for (var i = 0; i + 8 <= want; i += 8) {
                    var lo = dv.getUint32(i, true);
                    var hi = dv.getUint32(i + 4, true);
                    if (lo === 0 && hi === 0) continue;
                    cb(hi * 4294967296 + lo, numOf(at) + i);
                }
            }
            done += want;
        }
    }
}

function moduleRanges(m, prot) {
    var out = [];
    var lo = numOf(m.base), hi = lo + m.size;
    Process.enumerateRanges(prot).forEach(function (r) {
        var b = numOf(r.base);
        if (b >= lo && b < hi) out.push(r);
    });
    return out;
}

/* -------------------------------------------------------- stage 1: RTTI */

/*
 * Itanium ABI:
 *   type_info      { vptr, char* name }          name at +8
 *   vtable layout  [ offset_to_top, type_info*, fn0, fn1, ... ]
 *   a vptr stored in an object points at fn0, so type_info* sits at vptr-8.
 *
 * So: mangled name string -> the word that points at it is type_info+8
 *     -> the word that points at type_info is vtable[1] -> vptr = that+8.
 */
function rttiSweep(m) {
    log('[*] stage 1: RTTI sweep over ' + MOD);

    var ro = moduleRanges(m, 'r--');
    var rw = moduleRanges(m, 'rw-');
    var rx = moduleRanges(m, 'r-x');
    var dataRanges = ro.concat(rw);
    if (dataRanges.length === 0) dataRanges = rx;

    /* mangled names for RBX::Foo look like "N3RBX3FooE" */
    var names = {};         /* addrNum -> demangled-ish name */
    var nameCount = 0;
    var scanIn = ro.concat(rx);
    for (var i = 0; i < scanIn.length; i++) {
        var hits = [];
        try {
            hits = Memory.scanSync(scanIn[i].base, scanIn[i].size, '4e 33 52 42 58'); /* "N3RBX" */
        } catch (e) { hits = []; }
        for (var h = 0; h < hits.length; h++) {
            var s = cstr(hits[h].address, 160);
            if (s === null || s.charAt(s.length - 1) !== 'E') continue;
            names[numOf(hits[h].address)] = s;
            nameCount++;
        }
    }
    log('    mangled RBX type names: ' + nameCount);
    if (nameCount === 0) return { classes: {}, byVtable: {} };

    /* pass A: find type_info objects (word pointing at a name, minus 8) */
    var tiToName = {};
    var tiCount = 0;
    sweep(dataRanges, function (val, at) {
        var nm = names[val];
        if (nm !== undefined && (at % 8) === 0) {
            var ti = at - 8;
            if (tiToName[ti] === undefined) { tiToName[ti] = nm; tiCount++; }
        }
    });
    log('    type_info objects: ' + tiCount);

    /* pass B: find vtables (word pointing at a type_info) */
    var classes = {};       /* name -> vptr number */
    var byVtable = {};      /* vptr number -> name */
    var vtCount = 0;
    sweep(dataRanges, function (val, at) {
        var nm = tiToName[val];
        if (nm === undefined || (at % 8) !== 0) return;
        var vptr = at + 8;
        if (byVtable[vptr] !== undefined) return;
        /* vtable[0] must be offset_to_top; for a primary vtable that is 0 */
        var top;
        try { top = ptrOf(at - 8).readU64().toNumber(); } catch (e) { return; }
        if (top !== 0) return;
        byVtable[vptr] = nm;
        if (classes[nm] === undefined) { classes[nm] = vptr; vtCount++; }
    });
    log('    primary vtables: ' + vtCount);

    return { classes: classes, byVtable: byVtable };
}

function findClass(classes, shortName) {
    /* "DataModel" -> key "N3RBX9DataModelE" */
    var want = 'N3RBX' + shortName.length + shortName + 'E';
    if (classes[want] !== undefined) return classes[want];
    var keys = Object.keys(classes);
    for (var i = 0; i < keys.length; i++) {
        if (keys[i].indexOf(shortName + 'E') !== -1) return classes[keys[i]];
    }
    return null;
}

/* ------------------------------------------- stage 2: find a live DataModel */

function findInstancesByVtable(vptrNum, limit) {
    var pat = [];
    for (var i = 0; i < 8; i++) {
        pat.push(('0' + ((Math.floor(vptrNum / Math.pow(256, i))) & 0xff).toString(16)).slice(-2));
    }
    var pattern = pat.join(' ');

    var out = [];
    var ranges = Process.enumerateRanges('rw-');
    for (var r = 0; r < ranges.length && out.length < limit; r++) {
        if (ranges[r].size > 256 * 1024 * 1024) continue;
        var hits = [];
        try { hits = Memory.scanSync(ranges[r].base, ranges[r].size, pattern); } catch (e) { continue; }
        for (var h = 0; h < hits.length && out.length < limit; h++) {
            /* a vptr lives at object+0, so the hit address IS the object */
            if (numOf(hits[h].address) % 8 !== 0) continue;
            out.push(hits[h].address);
        }
    }
    return out;
}

/* ------------------------------------------- stage 3: probe member offsets */

var DM_CHILDREN = ['Workspace', 'Players', 'Lighting', 'ReplicatedStorage',
                   'ReplicatedFirst', 'StarterGui', 'StarterPack', 'SoundService',
                   'TeleportService', 'Chat', 'CoreGui', 'RunService'];

var MAX_PROBE = 0x300;

/*
 * Children on Roblox is shared_ptr<vector<shared_ptr<Instance>>>: one pointer
 * in the Instance, and the vector's {begin,end,cap} triple sits at a small
 * fixed delta inside what it points at. Probe the pointer and the delta.
 */
function probeChildren(obj, byVtable) {
    for (var o = 0; o < MAX_PROBE; o += 8) {
        var holder;
        try { holder = obj.add(o).readPointer(); } catch (e) { continue; }
        if (holder.isNull() || !readable(holder, 0x20)) continue;

        for (var d = 0; d <= 0x18; d += 8) {
            var begin, end;
            try {
                begin = holder.add(d).readPointer();
                end = holder.add(d + 8).readPointer();
            } catch (e) { continue; }
            if (begin.isNull() || end.isNull()) continue;

            var span = numOf(end) - numOf(begin);
            if (span <= 0 || span % 16 !== 0) continue;
            var count = span / 16;
            if (count < 1 || count > 20000) continue;
            if (!readable(begin, span > 256 ? 256 : span)) continue;

            /* every element must be an Instance: element+0 is an Instance*,
               and that Instance's vptr must be a class we found in stage 1 */
            var good = 0, checked = 0;
            for (var k = 0; k < count && k < 8; k++) {
                checked++;
                var child;
                try { child = begin.add(k * 16).readPointer(); } catch (e) { break; }
                if (child.isNull() || !readable(child, 8)) break;
                var vp;
                try { vp = numOf(child.readPointer()); } catch (e) { break; }
                if (byVtable[vp] !== undefined) good++;
            }
            if (checked > 0 && good === checked) {
                return { offset: o, delta: d, count: count, begin: begin };
            }
        }
    }
    return null;
}

function probeName(obj, expectSet) {
    for (var o = 0; o < MAX_PROBE; o += 8) {
        /* Name may be stored inline (std::string) or behind a pointer */
        var s = stdString(obj.add(o));
        if (s !== null && (expectSet === null || expectSet[s] === true)) {
            return { offset: o, indirect: false, value: s };
        }
        var q;
        try { q = obj.add(o).readPointer(); } catch (e) { continue; }
        if (q.isNull() || !readable(q, 0x18)) continue;
        s = stdString(q);
        if (s !== null && (expectSet === null || expectSet[s] === true)) {
            return { offset: o, indirect: true, value: s };
        }
    }
    return null;
}

function probeParent(child, parent) {
    var want = numOf(parent);
    for (var o = 0; o < MAX_PROBE; o += 8) {
        var q;
        try { q = child.add(o).readPointer(); } catch (e) { continue; }
        if (!q.isNull() && numOf(q) === want) return o;
    }
    return null;
}

/* ClassDescriptor: a pointer to a descriptor whose first std::string is the class name. */
function probeClassDescriptor(child, expected) {
    for (var o = 0; o < MAX_PROBE; o += 8) {
        var q;
        try { q = child.add(o).readPointer(); } catch (e) { continue; }
        if (q.isNull() || !readable(q, 0x40)) continue;
        for (var d = 0; d <= 0x30; d += 8) {
            var s = stdString(q.add(d));
            if (s === expected) return { offset: o, nameDelta: d };
        }
    }
    return null;
}

/* ----------------------------------------------------- stage 4: tree walk */

function makeReader(L) {
    return {
        name: function (inst) {
            var at = inst.add(L.Name);
            if (L.NameIndirect) {
                try { at = at.readPointer(); } catch (e) { return null; }
                if (at.isNull()) return null;
            }
            return stdString(at);
        },
        children: function (inst) {
            var out = [];
            var holder;
            try { holder = inst.add(L.Children).readPointer(); } catch (e) { return out; }
            if (holder.isNull()) return out;
            var begin, end;
            try {
                begin = holder.add(L.ChildrenDelta).readPointer();
                end = holder.add(L.ChildrenDelta + 8).readPointer();
            } catch (e) { return out; }
            if (begin.isNull() || end.isNull()) return out;
            var count = (numOf(end) - numOf(begin)) / 16;
            if (!(count > 0) || count > 20000) return out;
            for (var i = 0; i < count; i++) {
                try {
                    var c = begin.add(i * 16).readPointer();
                    if (!c.isNull()) out.push(c);
                } catch (e) { break; }
            }
            return out;
        },
        klass: function (inst, byVtable) {
            try {
                var nm = byVtable[numOf(inst.readPointer())];
                return nm === undefined ? '?' : nm;
            } catch (e) { return '?'; }
        }
    };
}

function walk(reader, byVtable, inst, depth, maxDepth, lines) {
    if (depth > maxDepth) return;
    var kids = reader.children(inst);
    for (var i = 0; i < kids.length && i < 40; i++) {
        var nm = reader.name(kids[i]);
        var pad = '';
        for (var s = 0; s < depth; s++) pad += '  ';
        lines.push('    ' + pad + (nm === null ? '<?>' : nm) + '  [' + reader.klass(kids[i], byVtable) + ']');
        walk(reader, byVtable, kids[i], depth + 1, maxDepth, lines);
    }
}

/* ------------------------------------------------------------------- main */

function main() {
    var m = Process.findModuleByName(MOD);
    if (m === null) {
        log('[!] ' + MOD + ' not loaded yet — attach after the client is at the menu');
        return;
    }
    log('[*] ' + MOD + '  base ' + m.base + '  size ' + hex(m.size));

    var rtti = rttiSweep(m);
    var classNames = Object.keys(rtti.classes);
    if (classNames.length === 0) {
        log('[!] no RBX vtables found. the build may be fully stripped of RTTI — use the static route.');
        return;
    }

    var baseNum = numOf(m.base);
    log('');
    log('[*] vtables (name = absolute = RVA)');
    classNames.sort();
    for (var i = 0; i < classNames.length; i++) {
        var v = rtti.classes[classNames[i]];
        log('    ' + classNames[i] + ' = ' + hex(v) + ' = ' + hex(v - baseNum));
    }

    var dmVt = findClass(rtti.classes, 'DataModel');
    if (dmVt === null) {
        log('');
        log('[!] no DataModel vtable — dumped what RTTI there is; stop here.');
        return;
    }
    log('');
    log('[*] stage 2: DataModel vtable ' + hex(dmVt) + ' (RVA ' + hex(dmVt - baseNum) + ')');

    var cands = findInstancesByVtable(dmVt, 64);
    log('    candidate DataModel objects: ' + cands.length);
    if (cands.length === 0) {
        log('[!] none live. join a game first, then re-run.');
        return;
    }

    /* the real DataModel is the one whose children are the services */
    var dm = null, childInfo = null;
    var expect = {};
    for (var e = 0; e < DM_CHILDREN.length; e++) expect[DM_CHILDREN[e]] = true;

    for (var c = 0; c < cands.length; c++) {
        var ci = probeChildren(cands[c], rtti.byVtable);
        if (ci === null || ci.count < 4) continue;
        /* confirm by naming one child against the known service list */
        var hit = false;
        for (var k = 0; k < ci.count && k < 12 && !hit; k++) {
            var kid;
            try { kid = ci.begin.add(k * 16).readPointer(); } catch (err) { break; }
            if (kid.isNull()) continue;
            if (probeName(kid, expect) !== null) hit = true;
        }
        if (hit) { dm = cands[c]; childInfo = ci; break; }
    }

    if (dm === null) {
        log('[!] found DataModel-shaped objects but could not confirm services. widen MAX_PROBE and re-run.');
        return;
    }

    log('');
    log('[*] stage 3: DataModel @ ' + dm + '  children=' + childInfo.count);

    var firstKid = childInfo.begin.readPointer();
    var nameInfo = probeName(firstKid, expect);
    if (nameInfo === null) {
        log('[!] child name probe failed');
        return;
    }
    var parentOff = probeParent(firstKid, dm);
    var kidClass = rtti.byVtable[numOf(firstKid.readPointer())];
    var shortClass = kidClass ? kidClass.replace(/^N3RBX\d+/, '').replace(/E$/, '') : null;
    var cdInfo = shortClass ? probeClassDescriptor(firstKid, shortClass) : null;

    var L = {
        Name: nameInfo.offset,
        NameIndirect: nameInfo.indirect,
        Children: childInfo.offset,
        ChildrenDelta: childInfo.delta,
        Parent: parentOff,
        ClassDescriptor: cdInfo ? cdInfo.offset : null,
        ClassDescriptorName: cdInfo ? cdInfo.nameDelta : null
    };

    log('    Instance::Name            = ' + hex(L.Name) + (L.NameIndirect ? '  (via pointer)' : '  (inline std::string)'));
    log('    Instance::Children        = ' + hex(L.Children) + '  (+' + hex(L.ChildrenDelta) + ' to vector begin)');
    log('    Instance::Parent          = ' + (L.Parent === null ? 'not found' : hex(L.Parent)));
    log('    Instance::ClassDescriptor = ' + (L.ClassDescriptor === null ? 'not found' : hex(L.ClassDescriptor) +
        '  (name at +' + hex(L.ClassDescriptorName) + ')'));

    log('');
    log('[*] stage 4: tree walk with those offsets');
    var reader = makeReader(L);
    var lines = [];
    walk(reader, rtti.byVtable, dm, 0, 1, lines);
    for (var li = 0; li < lines.length && li < 60; li++) log(lines[li]);
    if (lines.length === 0) log('    (empty — offsets are wrong)');

    var out = {
        module: MOD,
        moduleSize: m.size,
        dataModelVtableRVA: dmVt - baseNum,
        instance: L,
        vtableRVA: {}
    };
    for (var vi = 0; vi < classNames.length; vi++) {
        out.vtableRVA[classNames[vi]] = rtti.classes[classNames[vi]] - baseNum;
    }

    log('');
    log('[*] JSON (RVAs only — add the runtime base yourself)');
    log(JSON.stringify(out, null, 2));
}

try { main(); } catch (err) { log('[!] ' + err.stack); }
