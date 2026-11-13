# Recoil Ledger

Learns your own recoil pull per weapon / barrel / grip / sight, separately for center, lean Q and lean E, then replays it while you fire. External only: it reads raw mouse input and sends relative moves, and never touches the game process.

## Build (Windows, VS 2022)
```
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
build\Release\RecoilLedger.exe
```
Run Siege in **borderless** if you want to see the toast overlay.

## Use
1. Pick weapon and attachments, then set **In-game sens** to your real value.
2. **F7** (Train). In the range, ADS (RMB) and hold fire. Sprays only count once you've held **2s+**, and only if they stay in one stance (center / Q / E). Pull down the way you naturally would.
3. Train each lean too: hold Q or E the whole spray and it files the spray under that stance automatically.
4. **SAVE** writes that loadout's profile to `%APPDATA%\RecoilLedger\profiles`.
5. **F6** (Arm). Hold RMB + LMB and it replays the pull. Q/E switch the curve instantly mid-spray. Your own mouse movement still passes through, so you just track.
6. **F8** is the kill switch.

One spray already works. Every spray after that tightens the average, and the confidence ring shows how close it's getting.

## Tests
`cmake -S . -B build && cmake --build build && build/rl_tests` (portable, also runs on Linux).
