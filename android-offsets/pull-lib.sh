#!/usr/bin/env bash
# Pull the Roblox split APKs off a connected device and extract the arm64 native lib.
# Usage: ./pull-lib.sh [outdir]   (default: ./dump)
set -euo pipefail

PKG="${PKG:-com.roblox.client}"
OUT="${1:-./dump}"

command -v adb  >/dev/null || { echo "adb not found"; exit 1; }
command -v unzip >/dev/null || { echo "unzip not found"; exit 1; }

adb get-state >/dev/null 2>&1 || { echo "no device: run 'adb devices'"; exit 1; }

mkdir -p "$OUT/apk"

echo "[*] package paths"
paths="$(adb shell pm path "$PKG" | tr -d '\r' | sed 's/^package://')"
[ -n "$paths" ] || { echo "$PKG not installed"; exit 1; }
echo "$paths" | sed 's/^/    /'

echo "[*] pulling apks"
while IFS= read -r p; do
    [ -n "$p" ] || continue
    adb pull "$p" "$OUT/apk/" >/dev/null
    echo "    $(basename "$p")"
done <<< "$paths"

echo "[*] version"
{
    adb shell dumpsys package "$PKG" | tr -d '\r' | grep -m1 -E "versionName="
    adb shell dumpsys package "$PKG" | tr -d '\r' | grep -m1 -E "versionCode="
    adb shell getprop ro.product.cpu.abi | sed 's/^/abi=/' | tr -d '\r'
} | tee "$OUT/version.txt" | sed 's/^/    /'

echo "[*] locating libroblox.so"
found=0
for f in "$OUT/apk"/*.apk; do
    entry="$(unzip -l "$f" 2>/dev/null | awk '{print $4}' | grep -m1 'lib/arm64-v8a/libroblox.so' || true)"
    if [ -n "$entry" ]; then
        unzip -o -j "$f" "$entry" -d "$OUT" >/dev/null
        echo "    from $(basename "$f")"
        found=1
        break
    fi
done

if [ "$found" -eq 0 ]; then
    echo "    not in any apk — the lib is extracted on-device, trying /data/app (needs root)"
    libpath="$(adb shell "find /data/app -name libroblox.so -path '*arm64*' 2>/dev/null | head -1" | tr -d '\r')"
    if [ -n "$libpath" ]; then
        adb pull "$libpath" "$OUT/libroblox.so" >/dev/null && found=1 && echo "    pulled $libpath"
    fi
fi

[ "$found" -eq 1 ] || { echo "could not obtain libroblox.so"; exit 1; }

echo "[*] fingerprint"
size=$(stat -c %s "$OUT/libroblox.so" 2>/dev/null || stat -f %z "$OUT/libroblox.so")
sha=$( (sha256sum "$OUT/libroblox.so" 2>/dev/null || shasum -a 256 "$OUT/libroblox.so") | cut -d' ' -f1)
printf '    size   %s bytes\n    sha256 %s\n' "$size" "$sha"
printf 'size=%s\nsha256=%s\n' "$size" "$sha" >> "$OUT/version.txt"

echo "[*] arch check"
if command -v readelf >/dev/null; then
    readelf -h "$OUT/libroblox.so" | grep -E "Class|Machine" | sed 's/^/    /'
fi

echo
echo "done -> $OUT/libroblox.so"
echo "keep version.txt next to it. offsets are only valid for this exact sha256."
