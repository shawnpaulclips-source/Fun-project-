# RECOIL//LEDGER — Plan

## Context
Wren wants a PC tool for Rainbow Six Siege. It learns *his own* recoil control for each weapon and attachment setup. During training it records how he drags the mouse while spraying. Then, when it's enabled in game, it replays that pull while he fires, so he only has to track the target. It never moves the crosshair to aim and never touches the camera except for the learned pull-down. Every weapon/attachment setup trains and saves on its own, and accuracy improves as training sessions pile up.

Two things up front:
- **Assets:** no ripped Siege models or textures from GitHub dumps. That's Ubisoft's IP, and redistributing it is a takedown waiting to happen. Weapon *names*, operator lists and attachment slots are facts and go in a data file. Visuals are original vector silhouettes per weapon class plus a procedural muzzle-flash and recoil animation. They look better in a custom UI anyway.
- **Detection:** this tool reads Raw Input and writes with `SendInput`. It never opens the game process, so BattlEye has no memory or handle hooks to see. Ubisoft does run server-side input heuristics (Mousetrap-style) that flag too-perfect, repeatable corrections. Mitigation is built in: per-shot humanized noise drawn from his own recorded variance. The risk is a ban on his account and nothing more.

## Stack
- C++20, MSVC, CMake, Win32.
- Dear ImGui (docking branch) + D3D11. Custom theme: graphite #14161A, signal amber #F2A93B, muted teal #4FB3A9; Inter + JetBrains Mono; 6px rounding; tweened state transitions.
- ImPlot for spray/trajectory graphs.
- nlohmann/json for profiles. Libraries are vendored under `third_party/`.

## Layout
```
recoil-ledger/
  CMakeLists.txt
  data/weapons.json          # operators → primaries/secondaries, legal attachments per weapon
  assets/icons/*.svg         # original silhouettes: AR, SMG, LMG, DMR, shotgun, pistol, MP
  assets/fonts/
  src/
    main.cpp                 # window, D3D11 device, ImGui loop, tray icon
    app/App.{h,cpp}          # state machine: Idle / Training / Armed / Firing
    input/RawInput.{h,cpp}   # WM_INPUT, LMB state, relative dx/dy at 1 kHz with QPC timestamps
    input/Injector.{h,cpp}   # SendInput MOUSEEVENTF_MOVE, sub-pixel accumulator
    input/Hotkeys.{h,cpp}    # global hotkeys (RegisterHotKey + low-level hook for LMB)
    model/Loadout.{h,cpp}    # weapon + barrel + grip + sight → stable profile key
    model/Sample.{h,cpp}     # one spray: vector<{t_ms, dx, dy}>
    model/Trainer.{h,cpp}    # alignment, outlier rejection, weighted mean, variance, confidence
    model/Profile.{h,cpp}    # learned curve + stats, JSON (de)serialize
    model/Store.{h,cpp}      # %APPDATA%/RecoilLedger/profiles/<key>.json
    play/Player.{h,cpp}      # replay curve while LMB held, humanization, sens scaling
    ui/Theme.{h,cpp}
    ui/LoadoutPanel.cpp      # operator → weapon → attachment pickers with animated icon
    ui/TrainPanel.cpp        # live trace, sample list, confidence meter, SAVE
    ui/PlayPanel.cpp         # arm toggle, strength, smoothing, overlay of curve
    ui/Anim.{h,cpp}          # spring/tween helpers, muzzle flash particles, recoil kick on icon
```

## Data: `weapons.json`
```json
{ "weapons": [
  { "id": "r4c", "name": "R4-C", "class": "AR", "rpm": 860,
    "barrels": ["none","flash_hider","compensator","muzzle_brake","suppressor","extended"],
    "grips": ["none","vertical","angled"],
    "sights": ["1.0","1.5","2.0","2.5"],
    "operators": ["Ash"] } ] }
```
Filled for the full current roster by hand from public wiki tables (names and slots only). The weapon's `rpm` sets the expected shot interval in the trainer.

Profile key: `r4c|compensator|vertical|1.5`. Sight magnification matters: on-screen pull scales with the zoom-sensitivity multiplier, so each sight gets its own profile. A "derive from 1.0x" option scales the base profile and is marked low-confidence until he trains that sight.

## Capture (Training mode)
1. He picks the loadout, presses **Train**, and walks into a Siege practice area (Shooting Range / T-Hunt) with that loadout.
2. RawInput registers `RIDEV_INPUTSINK` so it captures while the game has focus. While LMB is down it logs `{qpc_t, dx, dy}`. On release, the spray closes as one `Sample`.
3. Sprays under 300 ms or with more than a 150 ms input gap get thrown out (tap-fire, alt-tab).
4. The live ImPlot trace shows the cumulative path (Σdx, Σdy) with the current mean overlaid.

## Learning (Trainer)
- **Resample** each spray to a fixed 5 ms grid of cumulative displacement, so it's sample-rate independent.
- **Strip aim intent:** his deliberate tracking pollutes the recoil signal. Fit a low-order polynomial to dx(t) and keep only the vertical channel plus the horizontal component that's consistent across sprays. Random horizontal tracking averages out across samples, while the real horizontal recoil drift survives the mean.
- **Align** with time-since-trigger (Siege recoil is deterministic per shot index, so the timing is aligned already). Light DTW within ±20 ms absorbs input latency jitter.
- **Outlier rejection:** compute the median trajectory, then drop sprays whose RMS distance from it is above 2.5× MAD.
- **Aggregate:** exponentially weighted mean (newer sprays weigh more, λ = 0.9 per session) gives `curve[t]`, and per-point standard deviation gives `sigma[t]`.
- **Confidence** = f(sample count, mean σ, how fast the mean is converging) mapped to 0–100. The UI shows it as a filled ring: amber under 60, teal at 80 and up.
- **SAVE** writes the profile: `{key, version, sens_at_capture, dpi, fov, curve[], sigma[], n, confidence, updated}`. Unsaved sprays live in memory, and switching loadout asks to save or discard.

## Playback (Armed mode)
- Arm with a hotkey (F6 default) and select the loadout in the panel or with a cycle hotkey (F7/F8), because he's tabbed into the game. A small toast overlay (topmost layered window, click-through) shows the active key.
- On LMB down: start the clock. At 1 kHz, `delta = curve[t] - curve[t-1]`, scaled by `strength` (0–150%) and by `current_sens / sens_at_capture`.
- Add humanization: Gaussian noise with that point's `sigma[t] × 0.6`, low-passed so it doesn't jitter.
- A sub-pixel accumulator keeps fractional movement from being lost and sends integer moves through `SendInput`.
- **His input passes through untouched.** Injected movement is additive and only covers the recoil part, so his tracking still works. If he's also pulling down by reflex, he turns `strength` down; the UI suggests a value from how much he pulled during the first armed sprays.
- Stops on LMB up, after the curve ends (mag empty), or on a hotkey kill switch.
- `LLMHF_INJECTED` filtering keeps the injected movement from being recorded back into training.

## UI
- Left: operator grid → weapon list → attachment chips. The weapon silhouette animates on change (slide plus spring) and kicks with a muzzle flash on every captured shot.
- Center: spray graph, all samples faint, mean bold, σ band shaded.
- Right: confidence ring, sample count, Train/Stop, **SAVE**, Arm toggle, strength/smoothing sliders, sens/DPI/FOV fields.
- Every control has hover, active and focus states. Panels fade-tween between modes.

## Build order
1. Window + D3D11 + ImGui theme + tray.
2. RawInput capture + live plot (verify capture with the game focused).
3. Loadout model + weapons.json + pickers.
4. Trainer + Profile + Store + SAVE.
5. Injector + Player + hotkeys + toast overlay.
6. Humanization, sens scaling, strength auto-suggest.
7. Original SVG icons → rasterized at startup (nanosvg), animations polish.

## Verification
- Unit tests (doctest) for resampling, outlier rejection and the mean/σ math, using synthetic sprays with known curves plus noise. The learned curve has to land within 0.5 px RMS.
- Capture test harness: a replay mode that feeds a saved Sample into the Player and draws the output in a test window, then checks that the integrated movement equals the curve.
- Manual: Notepad/desktop loop-back (injected moves show in a cursor trace window), then the Siege Shooting Range. Bullet groups get compared armed and unarmed on the same wall at the same sens.
