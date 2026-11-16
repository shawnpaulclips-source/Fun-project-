package com.workbench.apk.engine

import android.content.Context
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import org.json.JSONObject
import java.io.File

enum class Stage(val label: String) {
    PULL("Pull APKs"),
    MERGE("Merge splits"),
    ENGINE("Detect engine"),
    SMALI("Decompile smali"),
    JAVA("Java view"),
    RESOURCES("Decode resources"),
    PACK("Pack to Downloads"),
}

enum class StageStatus { PENDING, RUNNING, DONE, SKIPPED, FAILED }

data class StageState(
    val stage: Stage,
    val status: StageStatus = StageStatus.PENDING,
    val detail: String = "",
    val progress: Float = 0f,
)

data class Progress(
    val stages: List<StageState>,
    val log: List<String>,
    val finished: Boolean = false,
    val outputDir: String? = null,
    val parts: List<String> = emptyList(),
    val engine: String = "",
)

/**
 * On-device decompile pipeline. Non-root: installed APKs are world-readable, so we
 * copy them straight out of /data/app via PackageManager, then work on the copy.
 *
 * Verified-solid here: pull, engine fingerprint, packing, Downloads output.
 * Library-backed stages (smali via baksmali, Java via jadx, resources via ARSCLib)
 * call each library's primary documented API; pin the versions in app/build.gradle.kts
 * on first Gradle sync. IL2CPP C# stubs are a PC-only stage (Il2CppDumper is .NET).
 */
private const val JADX_DEX_LIMIT = 40_000_000L  // skip on-device jadx above this much dex

class Workbench(private val context: Context) {

    private val engineSigns = listOf(
        "unity-il2cpp" to "libil2cpp.so",
        "unity-mono" to "assets/bin/Data/Managed/Assembly-CSharp.dll",
        "unity" to "libunity.so",
        "flutter" to "libflutter.so",
        "react-native" to "index.android.bundle",
        "godot" to "libgodot_android.so",
    )

    fun run(pkg: String, singleZip: Boolean = false, treeUri: android.net.Uri? = null): Flow<Progress> = flow {
        val stages = Stage.values().map { StageState(it) }.toMutableList()
        val log = ArrayList<String>()
        fun snapshot(finished: Boolean = false, dir: String? = null, parts: List<String> = emptyList(), engine: String = "") =
            Progress(stages.toList(), log.toList(), finished, dir, parts, engine)
        fun set(stage: Stage, status: StageStatus, detail: String = "", progress: Float = 0f) {
            val i = stages.indexOfFirst { it.stage == stage }
            stages[i] = stages[i].copy(status = status, detail = detail, progress = progress)
        }
        fun say(line: String) { log.add(line) }

        val work = File(context.filesDir, "work/${pkg}").apply { deleteRecursively(); mkdirs() }

        try {
            // 1. PULL
            set(Stage.PULL, StageStatus.RUNNING); emit(snapshot())
            val apks = AppScanner.apkPaths(context, pkg)
            val apkDir = File(work, "apks").apply { mkdirs() }
            apks.forEachIndexed { i, f ->
                val dst = File(apkDir, f.name.ifBlank { "split_$i.apk" })
                f.copyTo(dst, overwrite = true)
                say("pulled ${dst.name}  ${"%.1f".format(dst.length() / 1e6)} MB")
                set(Stage.PULL, StageStatus.RUNNING, "${i + 1}/${apks.size}", (i + 1f) / apks.size)
                emit(snapshot())
            }
            set(Stage.PULL, StageStatus.DONE, "${apks.size} file(s)"); emit(snapshot())

            // 2. MERGE — ARSCLib ApkBundle merges split APKs into one module.
            set(Stage.MERGE, StageStatus.RUNNING); emit(snapshot())
            val merged = File(work, "merged.apk")
            EngineBridge.mergeSplits(apkDir, merged)
            say("merged -> ${merged.name}  ${"%.1f".format(merged.length() / 1e6)} MB")
            set(Stage.MERGE, StageStatus.DONE); emit(snapshot())

            // 3. ENGINE
            set(Stage.ENGINE, StageStatus.RUNNING); emit(snapshot())
            val allApks = (apkDir.listFiles()?.toList() ?: emptyList()) + merged
            val engine = fingerprint(allApks)
            val dexBytes = totalDexBytes(listOf(merged))
            say("engine: $engine")
            if (engine == "unity-il2cpp") say("note: IL2CPP C# stubs need the PC tool (Il2CppDumper is .NET)")
            set(Stage.ENGINE, StageStatus.DONE, engine); emit(snapshot(engine = engine))

            val out = File(work, "out").apply { mkdirs() }

            // 4. SMALI — baksmali disassembles every dex. Editable + rebuildable.
            set(Stage.SMALI, StageStatus.RUNNING); emit(snapshot(engine = engine))
            val smaliDir = File(out, "smali").apply { mkdirs() }
            val dexCount = EngineBridge.baksmali(merged, smaliDir) { done, total ->
                set(Stage.SMALI, StageStatus.RUNNING, "dex $done/$total", done.toFloat() / total)
            }
            say("smali: $dexCount dex disassembled")
            set(Stage.SMALI, StageStatus.DONE, "$dexCount dex"); emit(snapshot(engine = engine))

            // 5. JAVA — jadx reconstruction, read-only reference. Best-effort: never let
            // it kill the run. jadx holds every class in memory, so a huge app (TikTok:
            // hundreds of MB of dex) would OOM the process mid-decompile. Skip those; the
            // smali + resources + dump are the real output and are already done/next.
            set(Stage.JAVA, StageStatus.RUNNING); emit(snapshot(engine = engine))
            val javaDir = File(out, "java").apply { mkdirs() }
            if (dexBytes > JADX_DEX_LIMIT) {
                val mb = dexBytes / 1_000_000
                say("java: skipped — ${mb}MB of dex is too large for on-device jadx (use jadx on a PC)")
                set(Stage.JAVA, StageStatus.SKIPPED, "too large (${mb}MB dex)"); emit(snapshot(engine = engine))
            } else {
                try {
                    val javaFiles = EngineBridge.jadx(merged, javaDir)
                    say("java: $javaFiles files (reference only, does not recompile)")
                    set(Stage.JAVA, StageStatus.DONE, "$javaFiles files")
                } catch (t: Throwable) {
                    javaDir.deleteRecursively()
                    System.gc()
                    say("java: skipped — jadx ran out of memory (${t.javaClass.simpleName}); smali is unaffected")
                    set(Stage.JAVA, StageStatus.SKIPPED, "out of memory")
                }
                emit(snapshot(engine = engine))
            }

            // 6. RESOURCES — ARSCLib decodes manifest + resources to editable XML, no aapt2.
            set(Stage.RESOURCES, StageStatus.RUNNING); emit(snapshot(engine = engine))
            val resDir = File(out, "resources").apply { mkdirs() }
            EngineBridge.decodeResources(merged, resDir)
            // Native libs + IL2CPP metadata live in the arm64 split; harvest them from every
            // APK so the dump always carries libil2cpp.so / global-metadata.dat even if the
            // merge dropped them. That's the exact file set the mod work needs.
            val harvested = EngineBridge.harvestNative(allApks, File(resDir, "raw"))
            say("resources: decoded + $harvested native file(s) harvested")
            writeEditMe(out, pkg, engine)
            set(Stage.RESOURCES, StageStatus.DONE); emit(snapshot(engine = engine))

            // 7. PACK -> chosen folder, or Downloads/apk-workbench/<pkg>/
            set(Stage.PACK, StageStatus.RUNNING); emit(snapshot(engine = engine))
            val name = pkg.replace('.', '_')
            val staging = File(work, "zips").apply { deleteRecursively(); mkdirs() }
            val meta = JSONObject().apply { put("package", pkg); put("engine", engine); put("apk_sha256", Packer.sha256(merged)) }
            val parts = if (singleZip) {
                Packer.packSingle(out, staging, name, meta) { p ->
                    set(Stage.PACK, StageStatus.RUNNING, "writing one zip", p)
                }
            } else {
                Packer.pack(out, staging, name, meta) { p ->
                    set(Stage.PACK, StageStatus.RUNNING, "writing parts", p)
                }
            }
            set(Stage.PACK, StageStatus.RUNNING, "saving", 0f); emit(snapshot(engine = engine))
            val dirLabel = Output.publish(context, treeUri, pkg, parts) { p ->
                set(Stage.PACK, StageStatus.RUNNING, "saving", p)
            }
            staging.deleteRecursively()
            parts.forEach { say("${if (singleZip) "zip" else "part"} ${it.name}  ${"%.1f".format(it.length() / 1e6)} MB") }
            set(Stage.PACK, StageStatus.DONE, if (singleZip) "one zip" else "${parts.size} part(s)")
            say("saved to $dirLabel")
            emit(snapshot(finished = true, dir = dirLabel, parts = parts.map { it.name }, engine = engine))
        } catch (e: Throwable) {
            val active = stages.indexOfFirst { it.status == StageStatus.RUNNING }
            if (active >= 0) stages[active] = stages[active].copy(status = StageStatus.FAILED, detail = e.message ?: "failed")
            say("error: ${e.message}")
            emit(snapshot())
            throw e
        }
    }

    /** Honest, per-engine note on what the native layer is and how to actually read it. */
    private fun nativeNote(engine: String): String = when (engine) {
        "unity-il2cpp" -> "libil2cpp.so holds the logic (IL2CPP). Offsets via Il2CppDumper on a PC; patch bytes, no C# source."
        "unity-mono" -> "real C# DLLs under assets/bin/Data/Managed/ — decompile with ILSpy/dnSpy."
        "flutter" -> "Dart is AOT-compiled into libapp.so (not Java). jadx can't read it; use blutter on a PC for annotated asm."
        "react-native" -> "JS lives in assets/index.android.bundle — often minified; a JS beautifier makes it readable."
        else -> "native .so are C/C++ and may be obfuscated (e.g. ollvm). No clean source; Ghidra on a PC for disassembly."
    }

    // Scan every APK (base + splits + merged): native libs live in the arm64 split,
    // so a merge that drops them would otherwise mis-detect the engine.
    private fun fingerprint(apks: List<File>): String {
        val names = HashSet<String>()
        for (apk in apks) {
            if (!apk.isFile) continue
            runCatching {
                java.util.zip.ZipFile(apk).use { zf ->
                    val en = zf.entries()
                    while (en.hasMoreElements()) names.add(en.nextElement().name)
                }
            }
        }
        for ((label, token) in engineSigns) if (names.any { it.endsWith(token) || it.contains(token) }) return label
        return if (names.any { it.endsWith(".dex") }) "java-kotlin" else "unknown"
    }

    /** Total size of all classes*.dex across the APKs, to decide if on-device jadx is sane. */
    private fun totalDexBytes(apks: List<File>): Long {
        var total = 0L
        for (apk in apks) {
            if (!apk.isFile) continue
            runCatching {
                java.util.zip.ZipFile(apk).use { zf ->
                    val en = zf.entries()
                    while (en.hasMoreElements()) {
                        val e = en.nextElement()
                        if (e.name.endsWith(".dex") && !e.name.contains('/')) total += e.size
                    }
                }
            }
        }
        return total
    }

    private fun writeEditMe(out: File, pkg: String, engine: String) {
        File(out, "EDIT_ME.txt").writeText(
            """
            WHERE TO EDIT
            =============
            engine: $engine

            EDIT AND REBUILD (round-trips):
              smali/        Dalvik bytecode. Edit here.
              resources/    manifest, layouts, strings (ARSCLib XML).

            READ-ONLY REFERENCE (does NOT recompile):
              java/         jadx reconstruction. Read it to understand the smali.
              ${nativeNote(engine)}

            Rebuild + sign happens back in the app after you edit (or in the PC tool).
            Your signing key != Play Store key, so uninstall $pkg before installing a rebuild.
            """.trimIndent()
        )
    }

}
