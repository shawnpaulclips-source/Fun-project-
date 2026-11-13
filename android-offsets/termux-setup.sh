#!/data/data/com.termux/files/usr/bin/bash
# termux-setup.sh — stand up the whole dump toolchain on the phone, no PC.
# Rooted device (Magisk). Run inside Termux. `su` must work.
set -euo pipefail

ARCH="${ARCH:-android-arm64}"              # android-arm64 for 64-bit, android-arm for 32
FDIR="/data/local/tmp"                     # where frida-server lands on-device
PKG="${PKG:-com.roblox.client}"

say() { printf '\n[*] %s\n' "$*"; }

say "packages"
pkg update -y >/dev/null
pkg install -y python rizin unzip wget xz-utils tsu >/dev/null
# rizin = on-phone disassembler for the flag-global xref work
# tsu   = gives Termux a working `su` wrapper

say "python deps"
pip install -q --upgrade frida-tools rzpipe pyelftools

FVER="$(frida --version)"
say "frida $FVER — fetching matching frida-server ($ARCH)"
SRV="frida-server-${FVER}-${ARCH}"
if [ ! -f "$FDIR/frida-server" ]; then
    wget -q "https://github.com/frida/frida/releases/download/${FVER}/${SRV}.xz" -O "/tmp/${SRV}.xz"
    unxz -f "/tmp/${SRV}.xz"
    su -c "cp /tmp/${SRV} ${FDIR}/frida-server && chmod 755 ${FDIR}/frida-server"
    say "installed ${FDIR}/frida-server"
else
    say "frida-server already present at ${FDIR}/frida-server"
fi

say "killing any stale frida-server, starting fresh as root"
su -c "pkill -f frida-server 2>/dev/null; ${FDIR}/frida-server -D" || true
sleep 2

say "sanity: can we see processes?"
if frida-ps -U >/dev/null 2>&1; then
    frida-ps -Uai | grep -i roblox || echo "    (roblox not running — launch it, then re-check with: frida-ps -Uai | grep -i roblox)"
else
    echo "    frida-ps failed. Check: su works, SELinux not blocking (setenforce 0 while testing),"
    echo "    and that you put $PKG in the Zygisk DenyList so the anticheat ignores it."
fi

cat <<'NEXT'

toolchain up. from here, on the phone:

  # one-time per client update — get the binary + fingerprint:
  ./pull-lib.sh dump            # (adb version) OR pull the split yourself and drop libroblox.so in dump/

  # static pass (flags + vtable RVAs), pure python, no process:
  python3 rbx_static.py dump/libroblox.so -o dump/static.json

  # dynamic pass (live member offsets), Roblox must be IN A GAME:
  frida -U -n com.roblox.client -l rbx-dump.js -o dump/dynamic.txt

frida-server dies on reboot — restart it with:
  su -c "/data/local/tmp/frida-server -D"
NEXT
