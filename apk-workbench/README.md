# apk-workbench

Pull an app off your Android phone over adb, merge the split APKs, decompile it
(apktool, jadx, and Il2CppDumper for Unity IL2CPP), clean the output, rebuild it,
decompile the rebuilt APK again to check it survived, then pack the result into
zip parts that are each under 30 MB.

Runs on the PC the phone is plugged into. apktool, jadx and Il2CppDumper need a
desktop. Stdlib Python only.

## Setup (one time)

1. **Phone:** Settings → About phone → tap Build number 7 times. Developer options →
   USB debugging on. Plug in, accept the RSA prompt.
2. **Engines** go in `apk-workbench/tools/`:
   - `platform-tools/` (adb), from developer.android.com/tools/releases/platform-tools
   - `apktool.jar`, from github.com/iBotPeaches/Apktool/releases
   - `jadx/` (unzip the release), from github.com/skylot/jadx/releases
   - `APKEditor.jar`, from github.com/REAndroid/APKEditor/releases
   - `Il2CppDumper/` (the `Il2CppDumper.dll` build), from github.com/Perfare/Il2CppDumper/releases.
     Needs the .NET runtime (`dotnet`) on PATH. Optional, only for Unity IL2CPP apps.
3. Copy `workbench.example.json` to `workbench.json` and fix paths if you put things elsewhere.
4. Check the setup:
   ```
   python workbench.py check
   ```

## Use

```
python workbench.py list
python workbench.py run -p brawl
```

`run` asks you to pick the package if more than one matches, then asks for the folder
to save into (folder picker, or a prompt if no window is available). Pass `-o <folder>`
to skip the question. Pass `--force` to redo every stage. Stages are cached under
`work/<package>/.stamps`, so a rerun skips finished work.

Output in the chosen folder:

- `<package>_part01.zip`, `_part02.zip`, … each under 25 MB target, 30 MB hard cap
- Part 01 holds `MANIFEST.json` and `REASSEMBLE.txt`

Put every part here, then rebuild the tree with:

```
python workbench.py unpack <folder with the parts> <output folder>
```

## Signing the rebuilt APK

Rebuilt APKs are unsigned unless you set them up. Create your own key once:

```
keytool -genkeypair -v -keystore workbench.keystore -alias workbench -keyalg RSA -keysize 2048 -validity 10000
```

Put the path in `workbench.json` as `keystore`, the alias as `key_alias`, and the path to
`apksigner` (from Android build-tools) as `apksigner`. Set the password in the environment
for the run, never in the config file:

```
set APK_KS_PASS=yourpassword        (Windows cmd)
$env:APK_KS_PASS="yourpassword"     (PowerShell)
```

## What the stages do

| Stage     | What it does |
|-----------|--------------|
| pull      | `pm path` for every split APK, plus OBB if the app has one (best effort) |
| merge     | APKEditor combines the splits into one APK (single APK is copied as-is) |
| decompile | apktool (smali + resources), jadx (Java), Il2CppDumper (IL2CPP stubs), in parallel |
| cleanup   | removes junk files, apktool's build dir, and jadx resource copies that match apktool's, writes `cleanup_report.json` |
| rebuild   | `apktool b` from the cleaned tree, signed with your key if configured |
| verify    | decompiles the rebuilt APK with jadx and compares Java file and warning counts, writes `verify_report.json` |
| pack      | greedy split into zip parts, big files cut into `.partNNN` pieces with sha256 in the manifest |

## Limits

- IL2CPP games (Unity) don't give back readable C#. The IL2CPP output is Il2CppDumper's
  stubs: class, field and method names and signatures, not bodies.
- Packing compresses everything, so a large app takes a few minutes to pack.
- Rebuilt apps are not guaranteed to run. apktool round trips usually work for smali and
  resources. Native libs and dex are copied as-is.
