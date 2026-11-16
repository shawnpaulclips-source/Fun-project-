package com.workbench.apk.engine

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
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
class Workbench(private val context: Context) {

    private val engineSigns = listOf(
        "unity-il2cpp" to "libil2cpp.so",
        "unity-mono" to "assets/bin/Data/Managed/Assembly-CSharp.dll",
        "unity" to "libunity.so",
        "flutter" to "libflutter.so",
        "react-native" to "index.android.bundle",
        "godot" to "libgodot_android.so",
    )

    fun run(pkg: String): Flow<Progress> = flow {
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
            val engine = fingerprint(merged)
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

            // 5. JAVA — jadx reconstruction, read-only reference.
            set(Stage.JAVA, StageStatus.RUNNING); emit(snapshot(engine = engine))
            val javaDir = File(out, "java").apply { mkdirs() }
            val javaFiles = EngineBridge.jadx(merged, javaDir)
            say("java: $javaFiles files (reference only, does not recompile)")
            set(Stage.JAVA, StageStatus.DONE, "$javaFiles files"); emit(snapshot(engine = engine))

            // 6. RESOURCES — ARSCLib decodes manifest + resources to editable XML, no aapt2.
            set(Stage.RESOURCES, StageStatus.RUNNING); emit(snapshot(engine = engine))
            val resDir = File(out, "resources").apply { mkdirs() }
            EngineBridge.decodeResources(merged, resDir)
            say("resources: decoded to ${resDir.name}/")
            writeEditMe(out, pkg, engine)
            set(Stage.RESOURCES, StageStatus.DONE); emit(snapshot(engine = engine))

            // 7. PACK -> Downloads/apk-workbench/<pkg>/
            set(Stage.PACK, StageStatus.RUNNING); emit(snapshot(engine = engine))
            val name = pkg.replace('.', '_')
            val staging = File(work, "zips").apply { mkdirs() }
            val meta = JSONObject().apply { put("package", pkg); put("engine", engine); put("apk_sha256", Packer.sha256(merged)) }
            val parts = Packer.pack(out, staging, name, meta) { p ->
                set(Stage.PACK, StageStatus.RUNNING, "writing parts", p)
            }
            val dirLabel = publishToDownloads(pkg, parts)
            parts.forEach { say("part ${it.name}  ${"%.1f".format(it.length() / 1e6)} MB") }
            set(Stage.PACK, StageStatus.DONE, "${parts.size} part(s)")
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

    private fun fingerprint(merged: File): String {
        val names = ArrayList<String>()
        java.util.zip.ZipFile(merged).use { zf ->
            val en = zf.entries()
            while (en.hasMoreElements()) names.add(en.nextElement().name)
        }
        for ((label, token) in engineSigns) if (names.any { it.contains(token) }) return label
        return if (names.any { it.endsWith(".dex") }) "java-kotlin" else "unknown"
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
              ${if (engine.startsWith("unity")) "native logic lives in libil2cpp.so; patch bytes, no C# source on-device" else "native: standard .so libs"}

            Rebuild + sign happens back in the app after you edit (or in the PC tool).
            Your signing key != Play Store key, so uninstall $pkg before installing a rebuild.
            """.trimIndent()
        )
    }

    /** Copy parts into the public Downloads/apk-workbench/<pkg>/ via MediaStore. */
    private fun publishToDownloads(pkg: String, parts: List<File>): String {
        val sub = "apk-workbench/${pkg.replace('.', '_')}"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val resolver = context.contentResolver
            for (p in parts) {
                // Replace an existing part of the same name.
                resolver.delete(
                    MediaStore.Downloads.EXTERNAL_CONTENT_URI,
                    "${MediaStore.Downloads.RELATIVE_PATH}=? AND ${MediaStore.Downloads.DISPLAY_NAME}=?",
                    arrayOf("${Environment.DIRECTORY_DOWNLOADS}/$sub/", p.name),
                )
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, p.name)
                    put(MediaStore.Downloads.MIME_TYPE, "application/zip")
                    put(MediaStore.Downloads.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/$sub")
                    put(MediaStore.Downloads.IS_PENDING, 1)
                }
                val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: throw IllegalStateException("could not create ${p.name} in Downloads")
                resolver.openOutputStream(uri).use { os -> p.inputStream().use { it.copyTo(os!!) } }
                resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
            }
            return "Downloads/$sub"
        } else {
            @Suppress("DEPRECATION")
            val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), sub).apply { mkdirs() }
            for (p in parts) p.copyTo(File(dir, p.name), overwrite = true)
            return "Downloads/$sub"
        }
    }
}
