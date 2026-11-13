# Marginalia Loader

Tap-to-pick Android app that pulls an installed app's full split bundle into one zip for
analysis, and rebuilds+signs a bundle with a swapped native lib. This is the acquisition
front-end for the offset work in `../../android-offsets` — it gets `libroblox.so` off the
device and into your hands without adb or a PC.

## What it does

**Extract tab**
- Lists every installed app with icon, package, split count and on-disk size. Roblox floats
  to the top. Search box.
- Tap one, hit *Extract bundle to zip*. It copies `base.apk` + every `split_*.apk` into a
  single zip in the app's external files dir, and writes `manifest.json` into it with
  versionName, versionCode, split list, and the **sha256 + size of `libroblox.so`** — the
  fingerprint every offset set has to be pinned to.
- *Share / send* fires the system share sheet so you can push the zip straight to a chat,
  Drive, or your PC.
- No root. An app's installed APK paths (`sourceDir` + `splitSourceDirs`) are world-readable;
  this just reads and zips them.

**Repackage tab**
- Pick a bundle zip + a replacement `libroblox.so`. It rewrites the arm64 split with your lib
  (stored uncompressed so the loader can mmap it), re-signs every apk with a cached
  self-signed key via Google's `apksig` (real v1+v2+v3), and exports an installable set plus
  the `adb install-multiple` line.

## The honest part about repackaging

The signing key is Marginalia's, not Roblox's. A re-signed Roblox **will fail Roblox's own
signature and integrity checks** — you'll get `AndroidAnticheatKick` the moment you try to
play online. So repackaging is not a path to a modded client you log into.

What it *is* good for: an analysis build. Patch `libroblox.so` to pull in a Frida gadget
(`DT_NEEDED` injection with LIEF/patchelf on the `.so` before you feed it in here), swap it in,
sign, install — now you have Frida-level execution **without root** for dumping offsets and
poking the VM. That's the real use. A gadget-patched lib is one example of a "modified
libroblox.so"; the app doesn't care what you changed, it just swaps and signs.

## Build

No Android SDK in the authoring environment, so this ships as source — it is not a prebuilt,
signed APK. Build it one of two ways:

Android Studio (easiest):
```
open marginalia/loader/ in Android Studio, let it sync, Run.
```

CLI (needs the Android SDK + JDK 17):
```sh
cd marginalia/loader
gradle wrapper --gradle-version 8.9      # first time, to get the wrapper jar
./gradlew assembleDebug
# app/build/outputs/apk/debug/app-debug.apk
```

On the phone itself (Termux): install a JDK 17, the Android command-line tools + platform 34
+ build-tools, point `local.properties` at the SDK, then `./gradlew assembleDebug`. Heavier
than a desktop build but it works, and keeps the whole flow on-device.

## Where it sits in the flow

```
Marginalia Loader  →  bundle.zip (+ libroblox.so fingerprint)
        │
        ├─ send me the zip        →  I run android-offsets/rbx_static.py on the .so  (vtables, flags, structs)
        └─ dump on the phone      →  android-offsets/rbx-dump.js in a game           (live member offsets)
                                              │
                                              └─ both → wired into the Marginalia executor harness
```

## Layout

```
app/src/main/
  AndroidManifest.xml
  res/drawable/ic_launcher.xml        margin-mark icon
  res/xml/file_paths.xml              FileProvider share paths
  res/values/themes.xml
  java/com/marginalia/loader/
    MainActivity.kt                   tabs + header
    AppScanner.kt                     enumerate installed apps + apk paths
    Fingerprint.kt                    locate + sha256 libroblox.so
    Extractor.kt                      zip the splits + manifest.json
    Signer.kt                         apksig v1/v2/v3, cached self-signed key (BouncyCastle cert)
    Repackager.kt                     swap lib into the split, re-sign the set
    Share.kt                          FileProvider share sheet
    ui/                               Compose: theme, screens, progress
```
