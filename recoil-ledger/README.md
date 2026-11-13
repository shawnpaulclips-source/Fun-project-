# Recoil Ledger

Learns *your* recoil pull for each weapon / barrel / grip / sight, separately for center, lean Q and lean E, then replays it while you fire. External only: it reads raw mouse input on its own thread and sends relative moves, and never touches the game process.

## Build
1. Install **Build Tools for Visual Studio 2022** with the **Desktop development with C++** workload (one time).
2. Extract the zip and double-click **`build.bat`**.
3. It produces `RecoilLedger\RecoilLedger.exe` (static CRT, no redist needed) with `data\` and `assets\` next to it, then offers to launch.

All dependencies (Dear ImGui, nlohmann/json, stb_image) are vendored, so the build needs no internet.

## Use
1. Pick weapon and attachments, and set **In-game sens** to your real value.
2. **F7 Train.** In the range, ADS and hold fire. A spray only counts after **2s+** of continuous fire in one stance. Pull the way you naturally do.
3. Leans: hold Q or E for the whole spray and it's filed under that lean automatically.
4. **SAVE** stores that exact loadout (all three stances) to `%APPDATA%\RecoilLedger\profiles`.
5. **F6 Arm.** ADS + fire and it replays your pull. Holding Q/E switches to that lean's curve instantly, mid-spray. Your own mouse still works on top, so you just track.
6. **F8** kills everything.

One spray already works. More sprays tighten the average, and the ring shows confidence.

## Weapon art
Drop your own PNGs in `RecoilLedger\assets\weapons\<weapon id>.png` (side view, muzzle right, transparent background). The id is the `"id"` field in `data\weapons.json`, e.g. `r4c.png`. Weapons without a PNG use the built-in drawn silhouettes.

## Tests
`cmake -S . -B build -DRL_BUILD_TESTS=ON && cmake --build build && build/rl_tests`
