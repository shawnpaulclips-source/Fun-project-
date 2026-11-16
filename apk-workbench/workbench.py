#!/usr/bin/env python3
"""apk-workbench: pull an installed app off an Android phone over adb, merge split
APKs, decompile (apktool + jadx, plus Il2CppDumper for Unity IL2CPP games), clean
the output up, rebuild it, decompile the rebuilt APK again to confirm it survived,
then pack everything into zip parts that are each under 30 MB.

Stdlib only. Runs on the PC the phone is plugged into. Engine paths come from
workbench.json next to this file. `check` shows what is missing.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "workbench.json"

PART_LIMIT = 25_000_000      # target bytes per zip part; the upload cap is 30 MB
HARD_PART_CAP = 30_000_000   # any part over this is a bug and aborts the pack
CHUNK_SIZE = 20_000_000      # files above this are cut into raw pieces before packing
PART_HEADROOM = 256_000      # reserved in part 01 for MANIFEST.json and REASSEMBLE.txt
ENTRY_OVERHEAD = 300         # per-entry allowance for zip local and central headers
MANIFEST = "MANIFEST.json"
README_NAME = "REASSEMBLE.txt"

JUNK_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}

REASSEMBLE_TEXT = """\
These are split parts of one decompiled app.

1. Put every *_part*.zip in one folder.
2. Run:  python workbench.py unpack <that folder> <output folder>
   (or unzip them all into one folder by hand, then read MANIFEST.json)

Files over the chunk size were cut into <name>.partNNN pieces. MANIFEST.json lists
each one with its pieces in order and a sha256. Concatenate the pieces, in order,
into the original path, then check the sha256.
"""

DEFAULTS = {
    "adb": "tools/platform-tools/adb",
    "java": "java",
    "apktool_jar": "tools/apktool.jar",
    "jadx_bin": "tools/jadx/bin/jadx",
    "apkeditor_jar": "tools/APKEditor.jar",
    "il2cpp_dll": "tools/Il2CppDumper/Il2CppDumper.dll",
    "dotnet": "dotnet",
    "apksigner": "",
    "zipalign": "",
    "keystore": "",
    "key_alias": "",
    "ghidra_home": "",
}

# Engine fingerprints, checked against the merged APK's entry list in order.
ENGINE_SIGNS = [
    ("unity-il2cpp", "libil2cpp.so"),
    ("unity-mono", "assets/bin/Data/Managed/Assembly-CSharp.dll"),
    ("unity", "libunity.so"),
    ("flutter", "libflutter.so"),
    ("react-native", "index.android.bundle"),
    ("xamarin", "assemblies/Mono.Android.dll"),
    ("cocos2d", "libcocos2d"),
    ("godot", "libgodot_android.so"),
]


# ---------------------------------------------------------------- config / shell

def resolve(value: str) -> str:
    if not value:
        return ""
    if "/" not in value and "\\" not in value:
        return value  # bare command name, resolved through PATH
    p = Path(value)
    return str(p) if p.is_absolute() else str(HERE / p)


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return {k: (resolve(v) if isinstance(v, str) else v) for k, v in cfg.items()}


def sh(cmd, *, cwd=None, capture: bool = False, ok=(0,)) -> str:
    cmd = [str(c) for c in cmd]
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if res.returncode not in ok:
        tail = (res.stderr or res.stdout or "")[-3000:]
        raise RuntimeError(f"`{Path(cmd[0]).name} ...` exited {res.returncode}\n{tail}")
    return res.stdout if capture else ""


def adb(cfg: dict, serial: str | None, *args, capture: bool = False) -> str:
    cmd = [cfg["adb"]]
    if serial:
        cmd += ["-s", serial]
    return sh(cmd + list(args), capture=capture)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def safe_rel(rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or not rel:
        raise RuntimeError(f"unsafe path in archive: {rel!r}")
    return p


class Stamps:
    """Stage markers so a rerun skips finished work. --force clears them."""

    def __init__(self, root: Path, force: bool):
        self.dir = root / ".stamps"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.force = force

    def done(self, stage: str) -> bool:
        return not self.force and (self.dir / stage).exists()

    def mark(self, stage: str) -> None:
        (self.dir / stage).write_text("ok", encoding="utf-8")


# ---------------------------------------------------------------- device

def list_third_party(cfg: dict, serial: str | None) -> list[str]:
    out = adb(cfg, serial, "shell", "pm", "list", "packages", "-3", capture=True)
    return sorted(line.split(":", 1)[1].strip()
                  for line in out.splitlines() if line.startswith("package:"))


PKG_COMPONENT = re.compile(r"([a-zA-Z][\w]*(?:\.[a-zA-Z][\w]*)+)/[\w.$]+")


def detect_foreground(cfg: dict, serial: str | None) -> str | None:
    """The package of the app currently on screen. Reads dumpsys, keeps only
    components whose package is actually third-party installed, so the launcher,
    SystemUI and the shell itself never win."""
    installed = set(list_third_party(cfg, serial))
    for args in (("shell", "dumpsys", "activity", "activities"),
                 ("shell", "dumpsys", "window", "windows"),
                 ("shell", "dumpsys", "window")):
        try:
            out = adb(cfg, serial, *args, capture=True)
        except RuntimeError:
            continue
        for line in out.splitlines():
            low = line.lower()
            if not any(k in low for k in ("resumed", "mcurrentfocus", "mfocusedapp", "topresumed")):
                continue
            for pkg in PKG_COMPONENT.findall(line):
                if pkg in installed:
                    return pkg
    return None


def pick_package(cfg: dict, serial: str | None, query: str | None, auto: bool) -> str:
    if auto and not query:
        fg = detect_foreground(cfg, serial)
        if fg:
            print(f"auto-detected foreground app: {fg}")
            return fg
        print("auto-detect found nothing on screen; falling back to the list")
    pkgs = list_third_party(cfg, serial)
    if query:
        pkgs = [p for p in pkgs if query.lower() in p.lower()]
    if not pkgs:
        raise SystemExit("No matching third-party package on the device.")
    if len(pkgs) == 1:
        print(f"package: {pkgs[0]}")
        return pkgs[0]
    for i, p in enumerate(pkgs, 1):
        print(f"{i:3}  {p}")
    while True:
        raw = input("pick a number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(pkgs):
            return pkgs[int(raw) - 1]


def pull_apks(cfg: dict, serial: str | None, pkg: str, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    out = adb(cfg, serial, "shell", "pm", "path", pkg, capture=True)
    remote = [line.split(":", 1)[1].strip()
              for line in out.splitlines() if line.startswith("package:")]
    if not remote:
        raise RuntimeError(f"`pm path {pkg}` returned nothing")
    for r in remote:
        adb(cfg, serial, "pull", r, str(dest / Path(r).name))
    return sorted(dest.glob("*.apk"))


def pull_obb(cfg: dict, serial: str | None, pkg: str, dest: Path) -> list[Path]:
    """Best effort. Some Unity titles keep global-metadata.dat in the OBB."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        listing = adb(cfg, serial, "shell", "ls", f"/sdcard/Android/obb/{pkg}", capture=True)
    except RuntimeError:
        return []
    got = []
    for name in listing.split():
        if name.endswith(".obb"):
            target = dest / name
            adb(cfg, serial, "pull", f"/sdcard/Android/obb/{pkg}/{name}", str(target))
            got.append(target)
    return got


# ---------------------------------------------------------------- decompile

def detect_engine(merged: Path) -> dict:
    """Name the app's engine from what's inside the APK, so you know which tree is
    the one you can actually edit and rebuild."""
    with zipfile.ZipFile(merged) as z:
        names = z.namelist()
    found = []
    for label, token in ENGINE_SIGNS:
        if any(token in n for n in names) and label not in found:
            found.append(label)
    has_dex = any(n.endswith(".dex") for n in names)
    if not found and has_dex:
        found.append("java-kotlin")

    primary = found[0] if found else "unknown"
    rebuildable = "smali (apktool tree) — edit and `apktool b` round-trips"
    if primary in ("unity-il2cpp", "unity"):
        native = "native: logic is in libil2cpp.so; patch bytes/RVA, no C# source"
    elif primary == "unity-mono":
        native = "native: real C# DLLs under assets/bin/Data/Managed (decompile with ILSpy/dnSpy)"
    else:
        native = "native: none detected, or standard .so libs"

    return {
        "engine": primary,
        "all_signatures": found,
        "has_dex": has_dex,
        "editable_rebuildable": rebuildable,
        "native_note": native,
    }


def merge_apks(cfg: dict, apks: list[Path], out_apk: Path) -> None:
    if out_apk.exists():
        out_apk.unlink()
    if len(apks) == 1:
        shutil.copy2(apks[0], out_apk)
        return
    sh([cfg["java"], "-jar", cfg["apkeditor_jar"], "m", "-i", apks[0].parent, "-o", out_apk])


def extract_first(archives: list[Path], suffix: str, prefer: str, dest_dir: Path) -> Path | None:
    for archive in archives:
        with zipfile.ZipFile(archive) as z:
            hits = [n for n in z.namelist() if n.endswith(suffix)]
            if not hits:
                continue
            hits.sort(key=lambda n: prefer not in n)
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / Path(hits[0]).name
            with z.open(hits[0]) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            return target
    return None


def dump_il2cpp(cfg: dict, merged: Path, out: Path, obb_files: list[Path], workdir: Path) -> bool:
    """Returns False when the app is not an IL2CPP build."""
    if not Path(cfg["il2cpp_dll"]).exists():
        raise RuntimeError("Il2CppDumper.dll not found (set il2cpp_dll in workbench.json)")
    staging = workdir / "il2cpp_in"
    lib = extract_first([merged], "libil2cpp.so", "arm64-v8a", staging)
    if lib is None:
        return False
    meta = extract_first([merged, *obb_files], "global-metadata.dat", "", staging)
    if meta is None:
        raise RuntimeError("IL2CPP build, but global-metadata.dat is in neither the APK nor the OBB")
    out.mkdir(parents=True, exist_ok=True)
    sh([cfg["dotnet"], cfg["il2cpp_dll"], lib, meta, out])
    return True


def decompile(cfg: dict, merged: Path, out: Path, obb_files: list[Path], workdir: Path) -> None:
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    threads = str(os.cpu_count() or 4)

    def run_apktool():
        sh([cfg["java"], "-jar", cfg["apktool_jar"], "d", "-f", "-o", out / "apktool", merged])

    def run_jadx():
        sh([cfg["jadx_bin"], "-j", threads, "--show-bad-code", "-d", out / "jadx", merged],
           ok=(0, 1))

    def run_il2cpp():
        return dump_il2cpp(cfg, merged, out / "il2cpp", obb_files, workdir)

    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(run_apktool): "apktool",
                   ex.submit(run_jadx): "jadx",
                   ex.submit(run_il2cpp): "il2cpp"}
        for fut in cf.as_completed(futures):
            name = futures[fut]
            try:
                result = fut.result()
            except Exception as e:
                if name != "il2cpp":
                    raise
                print(f"  il2cpp: {e}")
                continue
            if name == "il2cpp":
                print("  il2cpp: dumped" if result else "  il2cpp: not an IL2CPP build, skipped")
            else:
                print(f"  {name}: done")


# ---------------------------------------------------------------- cleanup / rebuild / verify

def cleanup(out: Path) -> dict:
    report: dict = {"junk_removed": [], "duplicates_removed": [], "bytes_freed": 0}

    def drop(p: Path, bucket: str) -> None:
        report["bytes_freed"] += p.stat().st_size
        report[bucket].append(p.relative_to(out).as_posix())
        p.unlink()

    for p in list(out.rglob("*")):
        if p.is_file() and p.name in JUNK_FILES:
            drop(p, "junk_removed")

    # apktool's build/ is a rebuild artifact, and rebuild regenerates it.
    shutil.rmtree(out / "apktool" / "build", ignore_errors=True)

    # jadx copies raw resources next to its sources. Drop the ones byte-identical
    # to apktool's copy at the same path, since apktool's tree already has them.
    jres = out / "jadx" / "resources"
    if jres.exists():
        for p in list(jres.rglob("*")):
            if not p.is_file():
                continue
            twin = out / "apktool" / p.relative_to(jres)
            if twin.is_file() and twin.stat().st_size == p.stat().st_size and sha256(twin) == sha256(p):
                drop(p, "duplicates_removed")
        for d in sorted(jres.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()

    (out / "cleanup_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def run_ghidra(cfg: dict, merged: Path, out: Path, workdir: Path) -> None:
    """Optional. Export readable C pseudocode for libil2cpp.so with Ghidra headless.
    Slow (tens of minutes on a real game lib) but it's the only way to get native
    logic you can read. If Il2CppDumper produced a Ghidra script, it's applied first
    so functions carry their real names."""
    ghidra = cfg["ghidra_home"]
    headless = Path(ghidra) / "support" / ("analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
    if not ghidra or not headless.exists():
        print("  native: ghidra_home not set or analyzeHeadless missing, skipped")
        return
    staging = workdir / "il2cpp_in"
    lib = extract_first([merged], "libil2cpp.so", "arm64-v8a", staging)
    if lib is None:
        print("  native: no libil2cpp.so, skipped")
        return
    nat = out / "native"
    nat.mkdir(parents=True, exist_ok=True)
    postscript = nat / "_export_pseudocode.py"
    postscript.write_text(
        "# Ghidra postscript: dump decompiled C for every function.\n"
        "from ghidra.app.decompiler import DecompInterface\n"
        "from ghidra.util.task import ConsoleTaskMonitor\n"
        "import os\n"
        "ifc = DecompInterface(); ifc.openProgram(currentProgram)\n"
        "out = os.path.join(os.environ['GHIDRA_OUT'], 'libil2cpp.c')\n"
        "mon = ConsoleTaskMonitor()\n"
        "with open(out, 'w') as f:\n"
        "    for fn in currentProgram.getFunctionManager().getFunctions(True):\n"
        "        r = ifc.decompileFunction(fn, 60, mon)\n"
        "        if r and r.decompiledFunction:\n"
        "            f.write(r.decompiledFunction.getC())\n"
        "            f.write('\\n')\n",
        encoding="utf-8")
    proj = nat / "ghidra_proj"
    proj.mkdir(exist_ok=True)
    env_out = str(nat)
    cmd = [str(headless), str(proj), "wb", "-import", str(lib),
           "-scriptPath", str(nat), "-postScript", postscript.name, "-deleteProject"]
    il2cpp_script = out / "il2cpp" / "script.json"
    if il2cpp_script.exists():
        print("  native: Il2CppDumper script.json present; name it in Ghidra's GUI with the bundled script for named functions")
    env = dict(os.environ, GHIDRA_OUT=env_out)
    print("  native: running Ghidra headless (this takes a while)")
    subprocess.run([str(c) for c in cmd], env=env, capture_output=True, text=True)
    produced = nat / "libil2cpp.c"
    print(f"  native: {'wrote ' + str(produced) if produced.exists() else 'Ghidra produced no output'}")


def _align_and_sign(cfg: dict, unsigned: Path, signed: Path) -> str:
    """zipalign then apksigner (v1+v2+v3). Returns a one-line status."""
    staged = unsigned
    if cfg["zipalign"] and shutil.which(cfg["zipalign"]):
        aligned = unsigned.with_name("aligned.apk")
        sh([cfg["zipalign"], "-p", "-f", "4", unsigned, aligned])
        staged = aligned
    if cfg["apksigner"] and cfg["keystore"]:
        # Password comes from the APK_KS_PASS environment variable, never the config file.
        sh([cfg["apksigner"], "sign", "--ks", cfg["keystore"], "--ks-key-alias", cfg["key_alias"],
            "--ks-pass", "env:APK_KS_PASS",
            "--v1-signing-enabled", "true", "--v2-signing-enabled", "true",
            "--v3-signing-enabled", "true", "--out", signed, staged])
        sh([cfg["apksigner"], "verify", "--print-certs", signed], capture=True)
        return "aligned and signed (v1+v2+v3), signature verified"
    shutil.copy2(staged, signed)
    return "left unsigned (set zipalign, apksigner and keystore in workbench.json to get an installable APK)"


def build_apk(cfg: dict, apktool_dir: Path, signed: Path) -> Path:
    """apktool b from an edited smali tree, then align and sign."""
    signed.parent.mkdir(parents=True, exist_ok=True)
    unsigned = signed.with_name("unsigned.apk")
    sh([cfg["java"], "-jar", cfg["apktool_jar"], "b", "-f", apktool_dir, "-o", unsigned])
    status = _align_and_sign(cfg, unsigned, signed)
    for scratch in (unsigned, unsigned.with_name("aligned.apk")):
        scratch.unlink(missing_ok=True)
    print(f"  rebuild: {status}")
    return signed


def rebuild(cfg: dict, out: Path) -> Path:
    return build_apk(cfg, out / "apktool", out / "rebuilt" / "rebuilt.apk")


def count_java(tree: Path) -> tuple[int, int]:
    files = warns = 0
    if not tree.exists():
        return 0, 0
    for p in tree.rglob("*.java"):
        files += 1
        if "JADX WARN" in p.read_text(encoding="utf-8", errors="replace"):
            warns += 1
    return files, warns


def verify(cfg: dict, rebuilt_apk: Path, out: Path) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="verify_"))
    try:
        sh([cfg["jadx_bin"], "-j", str(os.cpu_count() or 4), "--show-bad-code",
            "-d", tmp, rebuilt_apk], ok=(0, 1))
        before = count_java(out / "jadx" / "sources")
        after = count_java(tmp / "sources")
        report = {
            "original_java_files": before[0], "original_warn_files": before[1],
            "rebuilt_java_files": after[0], "rebuilt_warn_files": after[1],
            "round_trip_ok": after[0] >= before[0] and after[1] <= before[1],
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    (out / "verify_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


# ---------------------------------------------------------------- pack / unpack

def deflated_size(path: Path, off: int, length: int) -> int:
    co = zlib.compressobj(6, zlib.DEFLATED, -15)
    total = 0
    with open(path, "rb") as f:
        f.seek(off)
        remaining = length
        while remaining > 0:
            block = f.read(min(1 << 20, remaining))
            if not block:
                break
            remaining -= len(block)
            total += len(co.compress(block))
    return total + len(co.flush())


def pack(src: Path, dest: Path, name: str, meta: dict,
         part_limit: int = PART_LIMIT, chunk_size: int = CHUNK_SIZE) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob(f"{name}_part*.zip"):
        old.unlink()

    entries: list[tuple[str, Path, int, int]] = []   # (arcname, source, offset, length)
    chunked: list[dict] = []
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src).as_posix()
        size = p.stat().st_size
        if size <= chunk_size:
            entries.append((rel, p, 0, size))
            continue
        pieces = []
        for i, off in enumerate(range(0, size, chunk_size)):
            arc = f"{rel}.part{i:03d}"
            entries.append((arc, p, off, min(chunk_size, size - off)))
            pieces.append(arc)
        chunked.append({"path": rel, "size": size, "sha256": sha256(p), "pieces": pieces})

    groups: list[list] = []
    cur: list = []
    used = PART_HEADROOM
    for arc, p, off, ln in entries:
        cost = int(deflated_size(p, off, ln) * 1.01) + ENTRY_OVERHEAD
        if cost > part_limit:
            raise RuntimeError(f"{arc} alone exceeds the part limit")
        if cur and used + cost > part_limit:
            groups.append(cur)
            cur, used = [], 0
        cur.append((arc, p, off, ln))
        used += cost
    groups.append(cur)

    part_names = [f"{name}_part{i:02d}.zip" for i in range(1, len(groups) + 1)]
    manifest = {**meta, "name": name, "parts": part_names, "chunked": chunked}
    mbytes = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(mbytes) > PART_HEADROOM:
        raise RuntimeError("manifest is larger than the space reserved for it")

    written = []
    for i, (group, pname) in enumerate(zip(groups, part_names)):
        target = dest / pname
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            if i == 0:
                z.writestr(MANIFEST, mbytes)
                z.writestr(README_NAME, REASSEMBLE_TEXT)
            for arc, p, off, ln in group:
                zi = zipfile.ZipInfo(arc)
                zi.compress_type = zipfile.ZIP_DEFLATED
                with z.open(zi, "w") as dst, open(p, "rb") as src_f:
                    src_f.seek(off)
                    remaining = ln
                    while remaining > 0:
                        block = src_f.read(min(1 << 20, remaining))
                        if not block:
                            break
                        remaining -= len(block)
                        dst.write(block)
        size = target.stat().st_size
        if size > HARD_PART_CAP:
            raise RuntimeError(f"{pname} is {size} bytes, over the {HARD_PART_CAP} cap")
        written.append(target)
    return written


def unpack(parts_dir: Path, out: Path) -> None:
    zips = sorted(parts_dir.glob("*_part*.zip"))
    if not zips:
        raise SystemExit("No *_part*.zip files found in that folder.")
    staging = Path(tempfile.mkdtemp(prefix="unpack_"))
    try:
        for z in zips:
            with zipfile.ZipFile(z) as zf:
                for member in zf.namelist():
                    safe_rel(member)
                zf.extractall(staging)

        manifest = json.loads((staging / MANIFEST).read_text(encoding="utf-8"))
        pieces = {piece for c in manifest["chunked"] for piece in c["pieces"]}
        out.mkdir(parents=True, exist_ok=True)

        for src in list(staging.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(staging).as_posix()
            if rel in (MANIFEST, README_NAME) or rel in pieces:
                continue
            dst = out / safe_rel(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

        for c in manifest["chunked"]:
            dst = out / safe_rel(c["path"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            with open(dst, "wb") as f:
                for piece in c["pieces"]:
                    with open(staging / piece, "rb") as pf:
                        shutil.copyfileobj(pf, f, 1 << 20)
            if sha256(dst) != c["sha256"]:
                raise RuntimeError(f"checksum mismatch after reassembly: {c['path']}")
            print(f"  rebuilt {c['path']} from {len(c['pieces'])} pieces, sha256 ok")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------- pipeline

def check_tools(cfg: dict) -> tuple[list[str], list[str]]:
    missing, optional = [], []

    def need(label: str, value: str) -> None:
        if not (value and shutil.which(value)):
            missing.append(f"{label}: {value or '(unset)'}")

    need("adb", cfg["adb"])
    need("java", cfg["java"])
    need("apktool_jar", cfg["apktool_jar"] if Path(cfg["apktool_jar"]).exists() else "")
    need("jadx_bin", cfg["jadx_bin"] if shutil.which(cfg["jadx_bin"]) or Path(cfg["jadx_bin"]).exists() else "")
    need("apkeditor_jar", cfg["apkeditor_jar"] if Path(cfg["apkeditor_jar"]).exists() else "")
    if not Path(cfg["il2cpp_dll"]).exists():
        optional.append(f"il2cpp_dll: {cfg['il2cpp_dll']} (Unity IL2CPP dump will be skipped)")
    if not shutil.which(cfg["dotnet"]):
        optional.append("dotnet: not found (needed for the IL2CPP dump)")
    if not (cfg["zipalign"] and (shutil.which(cfg["zipalign"]) or Path(cfg["zipalign"]).exists())):
        optional.append("zipalign: not set (rebuilt APK won't be aligned; apksigner may reject it)")
    if not (cfg["apksigner"] and cfg["keystore"]):
        optional.append("apksigner/keystore: not set (rebuilt APK will be unsigned and won't install)")
    if not cfg["ghidra_home"]:
        optional.append("ghidra_home: not set (--native pseudocode export will be skipped)")
    return missing, optional


def ask_output_dir(given: str | None) -> Path:
    if given:
        return Path(given)
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        chosen = filedialog.askdirectory(title="Save the zip parts to")
        root.destroy()
        if chosen:
            return Path(chosen)
    except Exception:
        pass
    return Path(input("folder to save the zip parts into: ").strip().strip('"'))


def run_pipeline(args: argparse.Namespace) -> None:
    cfg = load_config()
    missing, optional = check_tools(cfg)
    if missing:
        for m in missing:
            print(f"missing  {m}")
        raise SystemExit("Fix the missing tools (see `check`), then run again.")
    for o in optional:
        print(f"note     {o}")

    serial = args.device
    pkg = pick_package(cfg, serial, args.package, args.auto)
    work = Path(args.work).resolve() / pkg
    work.mkdir(parents=True, exist_ok=True)
    st = Stamps(work, args.force)
    apks_dir, obb_dir = work / "apks", work / "obb"
    merged, out = work / "merged.apk", work / "out"

    if st.done("pull"):
        apks = sorted(apks_dir.glob("*.apk"))
        obbs = sorted(obb_dir.glob("*.obb"))
    else:
        print("pull: APK splits")
        apks = pull_apks(cfg, serial, pkg, apks_dir)
        print("pull: OBB (best effort)")
        obbs = pull_obb(cfg, serial, pkg, obb_dir)
        st.mark("pull")
    print(f"  {len(apks)} APK split(s), {len(obbs)} OBB")

    if st.done("merge"):
        print("merge: cached")
    else:
        print("merge")
        merge_apks(cfg, apks, merged)
        st.mark("merge")

    engine = detect_engine(merged)
    print(f"engine: {engine['engine']}  (signatures: {', '.join(engine['all_signatures']) or 'none'})")

    if st.done("decompile"):
        print("decompile: cached")
    else:
        print("decompile: apktool + jadx + il2cpp")
        decompile(cfg, merged, out, obbs, work)
        (out / "engine.json").write_text(json.dumps(engine, indent=1), encoding="utf-8")
        (out / "EDIT_ME.txt").write_text(
            "WHERE TO EDIT\n"
            "=============\n"
            f"engine: {engine['engine']}\n\n"
            "EDIT AND REBUILD (round-trips perfectly):\n"
            "  out/apktool/smali*/   <- Dalvik bytecode. Edit here. `workbench.py rebuild` turns\n"
            "                           it back into an installable APK.\n"
            "  out/apktool/res/      <- resources, layouts, strings. Editable, rebuilds.\n"
            "  out/apktool/AndroidManifest.xml\n\n"
            "READ-ONLY REFERENCE (does NOT recompile):\n"
            "  out/jadx/sources/     <- Java reconstruction. Read it to understand the smali.\n"
            f"  {engine['native_note']}\n\n"
            "After editing smali, from the apk-workbench folder:\n"
            f"  python workbench.py rebuild \"{(out / 'apktool')}\" -o rebuilt.apk\n"
            "Then on the device (uninstall first; your key != Play Store key):\n"
            f"  adb uninstall {pkg}\n"
            "  adb install -r rebuilt.apk\n",
            encoding="utf-8")
        st.mark("decompile")

    if args.native and not st.done("native"):
        print("native: Ghidra pseudocode export")
        run_ghidra(cfg, merged, out, work)
        st.mark("native")

    if st.done("cleanup"):
        print("cleanup: cached")
    else:
        print("cleanup")
        report = cleanup(out)
        print(f"  removed {len(report['junk_removed'])} junk, "
              f"{len(report['duplicates_removed'])} duplicates, "
              f"freed {report['bytes_freed'] / 1e6:.1f} MB")
        st.mark("cleanup")

    rebuilt = work / "out" / "rebuilt" / "rebuilt.apk"
    if st.done("rebuild"):
        print("rebuild: cached")
    else:
        print("rebuild")
        rebuilt = rebuild(cfg, out)
        st.mark("rebuild")

    if st.done("verify"):
        print("verify: cached")
    else:
        print("verify: decompile the rebuilt APK again and compare")
        vr = verify(cfg, rebuilt, out)
        print(f"  original {vr['original_java_files']} java, {vr['original_warn_files']} with warnings")
        print(f"  rebuilt  {vr['rebuilt_java_files']} java, {vr['rebuilt_warn_files']} with warnings")
        print(f"  round trip ok: {vr['round_trip_ok']}")
        st.mark("verify")

    dest = ask_output_dir(args.out)
    name = pkg.replace(".", "_")
    print(f"pack: {name} -> {dest}")
    meta = {"package": pkg, "apk_sha256": sha256(merged)}
    parts = pack(out, dest, name, meta)
    total = sum(p.stat().st_size for p in parts)
    for p in parts:
        print(f"  {p}  {p.stat().st_size / 1e6:.1f} MB")
    print(f"done: {len(parts)} part(s), {total / 1e6:.1f} MB total")
    print(f"reassemble later: python workbench.py unpack \"{dest}\" <folder>")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="workbench", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="show missing engine paths")
    sub.add_parser("list", help="list third-party packages on the device")
    d = sub.add_parser("detect", help="print the app currently on screen")
    d.add_argument("-s", "--device", help="adb serial when more than one device is attached")

    r = sub.add_parser("run", help="pull, decompile, clean, rebuild, verify, pack")
    r.add_argument("-p", "--package", help="package name or a part of it, e.g. brawl")
    r.add_argument("--auto", action="store_true",
                   help="grab whatever app is open on the phone right now")
    r.add_argument("--native", action="store_true",
                   help="also export native pseudocode with Ghidra (slow; needs ghidra_home)")
    r.add_argument("-s", "--device", help="adb serial when more than one device is attached")
    r.add_argument("--work", default=str(HERE / "work"), help="scratch folder")
    r.add_argument("-o", "--out", help="folder for the zip parts (folder picker if omitted)")
    r.add_argument("--force", action="store_true", help="redo every stage")

    b = sub.add_parser("rebuild", help="rebuild an installable APK from an edited apktool tree")
    b.add_argument("apktool_dir", help="the out/apktool folder you edited")
    b.add_argument("-o", "--out", default="rebuilt.apk", help="output APK path")

    u = sub.add_parser("unpack", help="reassemble zip parts into a folder")
    u.add_argument("parts", help="folder holding the *_part*.zip files")
    u.add_argument("dest", help="folder to rebuild the app tree into")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "check":
            missing, optional = check_tools(load_config())
            for m in missing:
                print(f"missing  {m}")
            for o in optional:
                print(f"note     {o}")
            if not missing:
                print("all required tools found")
            return 0
        if args.cmd == "list":
            for p in list_third_party(load_config(), None):
                print(p)
            return 0
        if args.cmd == "detect":
            fg = detect_foreground(load_config(), args.device)
            print(fg or "nothing detected on screen")
            return 0 if fg else 1
        if args.cmd == "run":
            run_pipeline(args)
            return 0
        if args.cmd == "rebuild":
            cfg = load_config()
            apk = build_apk(cfg, Path(args.apktool_dir), Path(args.out).resolve())
            print(f"built {apk}")
            return 0
        if args.cmd == "unpack":
            unpack(Path(args.parts), Path(args.dest))
            print(f"reassembled into {args.dest}")
            return 0
    except (RuntimeError, SystemExit) as e:
        if isinstance(e, SystemExit):
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
