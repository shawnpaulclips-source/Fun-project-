# Android Roblox offsets

There is no list to download. Every public offset repo targets `RobloxPlayerBeta.exe` on
Windows x64 — different compiler, different ABI, different struct layout, and Byfron sitting
on top of it. The Android client is `libroblox.so`, ARM64, built with Clang/libc++. You dump
it yourself, and you re-dump it every client update.

## 0. What you need

- A device you can `adb` into. Root (Magisk) makes this far easier but is not strictly required.
- `adb`, `unzip`, Python 3.
- Frida for the dynamic route: `pip install frida-tools`.
- Ghidra 11+ for the static route (IDA works too; the script here is Ghidra).

The client has kick reasons for all of this baked into its enums — `AndroidAnticheatKick`,
`AndroidEmulatorKick`, `AndroidRootedKick`. So: a physical device beats an emulator, and if
you are rooted, put `com.roblox.client` in the Zygisk DenyList before you start. Expect to
lose the account you do this on.

## 1. Get the binary

```sh
./pull-lib.sh dump
```

Roblox ships as a split bundle, so `pm path` returns several APKs. The native lib is inside
`split_config.arm64_v8a.apk` at `lib/arm64-v8a/libroblox.so`. The script pulls every split,
finds the one carrying the lib, extracts it, and writes `version.txt` with versionName,
versionCode, size and sha256.

**That sha256 is the only thing that makes your offsets meaningful.** Offsets are RVAs into
one exact build. Keep the fingerprint beside them or you will debug a crash that is just a
stale number.

Grab `arm64-v8a`, not `armeabi-v7a`. Everything current is 64-bit.

## 2. Dynamic route — Frida (do this one first)

Faster, and it resolves real member offsets instead of guesses.

```sh
frida-ps -Uai | grep -i roblox            # confirm the package
frida -U -n com.roblox.client -l rbx-dump.js -o offsets.txt
```

Attach once the client is **in a game**, not at the menu — stage 2 needs a live DataModel on
the heap.

What it does:

1. **RTTI sweep.** Itanium ABI leaves the mangled type name of every polymorphic class in
   read-only data: `N3RBX9DataModelE`, `N3RBX5PartE`, and so on. A `std::type_info` holds a
   pointer to that string at +8; a vtable holds a pointer to the `type_info` in slot 1; and
   the vptr an object stores at +0 points one slot past that. So you chase the string
   backwards to the vtable and you get a complete class → vtable map with zero prior
   knowledge. That map is the single most useful primitive you can have: given any object
   pointer, read +0 and you know what it is.

2. **Heap sweep.** Scan `rw-` ranges for the DataModel vptr value. Each hit is a candidate
   object, because the vptr lives at offset 0.

3. **Member probe.** For each candidate, walk +0x0..0x300 in 8-byte steps looking for the
   `shared_ptr<vector<shared_ptr<Instance>>>` shape: a pointer whose target holds a
   `{begin, end, cap}` triple where `end - begin` divides by 16 and every element's first
   qword is a vtable from step 1. That is `Instance::Children`. Then it names a child and
   checks it against the guaranteed service list — `Workspace`, `Players`, `Lighting` — which
   both confirms the object really is the DataModel and locates `Instance::Name`. Parent is
   whichever pointer in a child equals the DataModel address. ClassDescriptor is whichever
   pointer leads to a struct whose first `std::string` matches the class name RTTI already
   gave you.

4. **Proof.** It walks the tree two levels deep and prints it with class tags. If that output
   looks like a real DataModel, the offsets are right. If it is empty or garbage, they aren't.

Strings are the one place to be careful. libc++ has two `std::string` layouts and the script
probes both:

| | long | short |
|---|---|---|
| default (NDK) | `cap@0` with is_long in LSB, `size@8`, `data@16` | `size<<1 @0`, chars at +1 |
| alternate | `data@0`, `size@8`, `cap@16` with is_long in MSB | `size@0`, chars at +1 |

This is also the clearest reason PC offsets cannot port: MSVC's `std::string` is
`{buf[16], size, cap}`, a completely different shape in a completely different place.

### Non-root Frida

If you cannot run `frida-server`, inject the gadget instead: pull the splits, drop
`libfrida-gadget.so` plus a config into the arm64 split's `lib/arm64-v8a/`, add a
`System.loadLibrary` call or rely on the gadget's constructor, re-sign all splits with the
same key, and install with `adb install-multiple`. Splits make this tedious but it works.
Gadget mode is also quieter than `frida-server` on its default port 27042.

## 3. Static route — Ghidra

For what the dynamic route cannot reach: FFlags, function addresses, anything you need before
the process is live.

```sh
# import dump/libroblox.so as AARCH64:LE:64:v8A, then:
analyzeHeadless ~/proj rbx -process libroblox.so \
    -postScript ghidra_rbx_rtti.py ~/rbx-static.json
```

Auto-analysis on a ~150MB stripped ARM64 binary takes hours. Turn off Decompiler Parameter ID
and the aggressive instruction finder for the first pass, let it finish, then re-enable.

`ghidra_rbx_rtti.py` does the same RTTI walk statically and gives you vtable RVAs without
running the client. It also collects every `FFlag*` / `FInt*` / `FString*` name string with
its RVA, which is how you get flag globals: xref the name string, land in the registration
code, and read which static the flag object gets written into. That static is what you toggle.
Same technique the Windows `FFlags.hpp` files are built with.

Other anchors worth xrefing, in order of how fast they pay off:

- Property name literals — `"Health"`, `"WalkSpeed"`, `"CFrame"`, `"Transparency"`. These land
  in Roblox's reflection descriptors, and a property descriptor carries either a direct member
  offset or an accessor you can read the offset out of. This is the cheapest path to
  `Humanoid::Health` and friends.
- Error and assert strings — they sit inside the function that failed, which hands you a named
  function in a sea of `FUN_`.
- Class name literals next to the descriptor tables, which map descriptor → class.

## 4. Value scanning, when you just want one number

GameGuardian on a rooted device, region `Ca`/`A`, search your health as a float, take damage,
refine. Then pointer-scan upward until you reach something static relative to the `.so` base.
Crude, no disassembler, and it finds a usable chain in minutes. Good for a single value, bad
for building anything structured — the chain breaks on every update and tells you nothing
about layout.

## 5. Keep them

```
offsets/
  version-<sha256-prefix>.json
```

Store RVAs, never absolute addresses — ASLR moves the base every launch. Resolve at runtime
with `Module.findBaseAddress("libroblox.so")` (Frida) or by parsing `/proc/self/maps`.

Re-run `rbx-dump.js` after every client update. The RTTI sweep is version-independent, so the
class map always rebuilds itself; the member offsets are what move, and those get re-probed
automatically. That is the whole point of probing rather than hardcoding.

## Files

| | |
|---|---|
| `pull-lib.sh` | pull splits, extract `libroblox.so`, fingerprint it |
| `rbx-dump.js` | Frida: RTTI sweep, DataModel discovery, member probe, tree proof, JSON |
| `ghidra_rbx_rtti.py` | Ghidra: static RTTI → vtable RVAs, plus flag-name string RVAs |

---

# Faster, and fully on-phone

Two speed problems: the dump is slow, and you were planning to shuttle the binary to a PC.
Both go away on a rooted device.

## No PC in the loop

`termux-setup.sh` stands the whole toolchain up on the phone itself — Python, Frida,
frida-server (version-matched and started as root), rizin, and the Python deps. After it runs
you dump offsets on the device and never touch a desktop:

```sh
./termux-setup.sh
python3 rbx_static.py dump/libroblox.so -o dump/static.json   # flags + vtables, no process
frida -U -n com.roblox.client -l rbx-dump.js -o dump/dynamic.txt  # live member offsets, in a game
```

Put `com.roblox.client` in the Zygisk DenyList first, and `setenforce 0` while you test if
SELinux blocks the attach. frida-server dies on reboot; `su -c "/data/local/tmp/frida-server -D"`
brings it back.

## Static pass runs on the phone now

`rbx_static.py` replaces Ghidra for the parts that don't need a disassembler. It reads the ELF
sections directly and does the RTTI walk in pure Python — vtable RVAs and every FFlag/FInt/
FString name string, in seconds, on-device. Ghidra was the bottleneck (hours of auto-analysis
on a 150MB stripped binary); you only need it, or rizin on the phone, for the one thing the
static sweep can't finish: xref'ing a flag-name string to the global it registers into. rizin
is in the setup for exactly that follow-up.

## Dynamic pass got a fast mode

`rbx-dump.js` now defaults `FAST = true`:

- Heap regions scanned largest-first — the DataModel lives in a big allocation, so it surfaces
  almost immediately instead of after grinding every small mapping.
- Stops at the first confirmed DataModel instead of collecting 64 candidates.
- Caches the winning page to `/data/local/tmp/rbx_dm_hint.txt` and tries it first next run, so
  repeat dumps skip the sweep entirely.

## Don't re-dump everything each update

The RTTI sweep is version-independent — the class→vtable map rebuilds itself free every run.
What actually moves between updates is the member offsets and the flag table. So after an
update: re-run the dynamic pass (fast, cached), and diff the new `static.json` flag RVAs
against the old. You're chasing a handful of changed numbers, not redoing the whole dump.

---

# Where this is going — Marginalia

The dump is step one. The thing it feeds is **Marginalia** — the notes you write in the margins
of someone else's text. The build splits into three layers, and it's worth being straight about
which are easy and which is the grind:

- **UI + script layer** — floating launch button, panel, editor, the feature toggles, the script
  library. Pure client-side. No offsets needed, builds independently.
- **Execution harness** — Frida injects into `libroblox.so`, finds the Luau global state off the
  offsets `rbx-dump.js` produces, grabs a thread, bumps identity, runs Luau. This is the real
  executor core and it wires directly to `dynamic.json`.
- **VM / identity / integrity** — the part that is a moving target. Roblox mutates the Luau VM,
  encrypts bytecode, and ships anticheat changes on a weekly cadence. This is the layer every
  named executor burns a team on, re-done every update. I build against your dump; it is not a
  one-and-done.

The executor is **not inside the APK**. Decompiling Roblox gets you the offsets and structure to
attack it with — the executor is separate software that does the attacking. So the order is: get
a clean dump off your phone, send me `static.json` + `dynamic.json`, and I wire the harness. The
UI and the 30-feature layer I start regardless, since they don't wait on offsets.
