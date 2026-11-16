# Workbench (Android)

The PC workbench, ported to an on-device Android app. Pick an installed app, and it
pulls the APK, merges splits, decompiles to smali + Java + resources, and writes zip
parts under 30 MB into Downloads — all on the phone, **no root, no PC**. Animated
Compose UI.

## Why it works without root

Installed APKs (`base.apk` + splits) live at `/data/app/...` and are **world-readable**.
`PackageManager` hands you their paths, so the app copies them straight out — no root.
App *private data* stays off-limits; the APK files themselves don't.

## Capability map (read this before expecting magic)

| Capability | On-device | Notes |
|---|---|---|
| Pull installed APK + splits | yes | world-readable, no root |
| Merge splits | yes | ARSCLib `ApkBundle` |
| smali (edit + rebuild) | yes | baksmali / smali, pure JVM |
| Java view | yes, read-only | jadx; does **not** recompile |
| Resources decode | yes | ARSCLib, no native aapt2 |
| Rebuild + sign | yes | smali→dex swap + apksig v1/v2/v3 |
| **IL2CPP C# stubs** | **no** | Il2CppDumper is .NET — PC tool only |
| Ghidra native pseudocode | no | PC tool only |

So for a Unity IL2CPP game like Brawl Stars, on-device you get smali, resources and the
Java shell. The C# dump stays a PC job. The real mod surface (ESP/aim/radar) is native
(`libil2cpp.so`), reached by Frida/patching, not an apktool rebuild either way.

## Build the APK

No prebuilt APK is committed. Build it:

1. Open `apk-workbench-android/` in Android Studio (Koala or newer), or run `./gradlew assembleRelease`.
2. First Gradle sync pulls the engine libraries (ARSCLib, smali/baksmali, jadx, apksig).
3. Install the output APK on the S22.

**This project has not been compiled against a device yet** (built in an environment
without the Android SDK). Expect the first Gradle sync to pin exact library versions;
if a version bumped and an engine call moved, the only file to touch is
`engine/EngineBridge.kt` — it isolates every decompile/rebuild library call behind
reflection against the versions in `app/build.gradle.kts`, so nothing else changes.

## How it runs

1. **Pick** — the app lists third-party apps (icon, size, split count), largest first.
   Grant usage access and it can also surface the most recently used app.
2. **Pipeline** — animated stepper: pull → merge → engine → smali → Java → resources → pack.
   Live log underneath.
3. **Done** — zip parts land in `Downloads/apk-workbench/<package>/`, each under 30 MB.
   That folder is what you hand back to add features to.

## Signing rebuilds

Rebuilds are signed with a key generated in **AndroidKeyStore** on first use — a
self-signed cert comes free, no keystore file, no password to manage. It's your key, not
the Play Store's, so a rebuilt app installs only after you uninstall the original, and it
never updates the store copy. Uninstalling drops local data, so back up a save first if
it isn't cloud-synced.

## Layout

```
app/src/main/java/com/workbench/apk/
  MainActivity.kt            screen state machine
  ui/Theme.kt                graphite / amber / teal, dark only
  ui/PickerScreen.kt         animated app list + search + spinner
  ui/PipelineScreen.kt       animated stage stepper + live log
  ui/DoneScreen.kt           result + zip parts
  engine/AppScanner.kt       PackageManager list, APK paths, usage-stats recent app
  engine/Workbench.kt        pipeline orchestration, Flow of progress, Downloads output
  engine/EngineBridge.kt     the one file that calls the decompile/rebuild libraries
  engine/Signer.kt           AndroidKeyStore key + apksig v1/v2/v3
  engine/Packer.kt           <30 MB zip split + verified reassembly
```

The `Packer` split format matches the PC tool exactly, so `workbench.py unpack` on a PC
reassembles the phone's output and vice versa.
