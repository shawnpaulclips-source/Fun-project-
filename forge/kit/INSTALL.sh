#!/usr/bin/env bash
# forge-kit installer. Verified on Ubuntu 24.04 / Python 3.13.18 / no GPU.
set -euo pipefail

echo "[1/3] GL libraries (Blender 5.x will not start without these, even headless)"
if [ "$(id -u)" -eq 0 ]; then APT=""; else APT="sudo"; fi
$APT apt-get update -qq
$APT apt-get install -y -qq \
  libegl1 libgl1 libglx-mesa0 libgl1-mesa-dri mesa-vulkan-drivers libvulkan1 \
  libopengl0 libxi6 libxxf86vm1 libxfixes3 libxrender1 libsm6 libxkbcommon0 \
  xvfb ffmpeg

echo "[2/3] Blender as a Python module (~1.1 GB)"
PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2]==(3,13) else 1)'; then
  echo "ERROR: bpy==5.3.0 requires Python 3.13.x exactly. Found: $("$PY" -V)" >&2
  exit 1
fi
"$PY" -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q 'bpy==5.3.0'

echo "[3/3] smoke test"
./venv/bin/python smoke_test.py

cat <<'MSG'

Installed. Run an example:

  ./venv/bin/python examples/01_armillary.py     out/   # detailed hard-surface asset
  ./venv/bin/python examples/02_rig_character.py out/   # Rigify rig + weight heatmap
  ./venv/bin/python examples/03_walk_cycle.py    out/   # block -> spline -> critique

Read README.md first. The trap table in section 2 will save you an afternoon.
MSG
