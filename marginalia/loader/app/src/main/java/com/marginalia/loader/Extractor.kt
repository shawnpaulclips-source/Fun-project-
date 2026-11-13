package com.marginalia.loader

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

enum class Stage(val label: String) {
    READ("Reading package"),
    UNPACK("Unpacking splits"),
    HASH("Hashing libroblox.so"),
    SEAL("Sealing zip + manifest");

    companion object { val ordered = entries.toList() }
}

sealed class Step {
    data class Progress(val stage: Stage, val stageFraction: Float, val note: String) : Step()
    data class Done(val zip: File, val lib: LibInfo?) : Step()
    data class Failed(val reason: String) : Step()
}

object Extractor {

    /**
     * Zip every apk of [entry] (base + splits) into one bundle in external files,
     * with a manifest.json carrying version + the libroblox.so fingerprint.
     * No root: these apk paths are world-readable. Emits per-stage progress.
     */
    fun extract(context: Context, entry: AppEntry, emit: (Step) -> Unit): File? {
        val dir = File(context.getExternalFilesDir(null), "bundles").apply { mkdirs() }
        val out = File(dir, "${entry.pkg}-${entry.versionCode}.zip")
        if (out.exists()) out.delete()

        emit(Step.Progress(Stage.READ, 0f, "locating apks"))
        val apks = entry.apkPaths.map(::File).filter { it.exists() }
        if (apks.isEmpty()) { emit(Step.Failed("no readable apk paths")); return null }
        emit(Step.Progress(Stage.READ, 1f, "${apks.size} apk(s)"))

        val total = apks.sumOf { it.length() }.coerceAtLeast(1)
        var written = 0L

        try {
            ZipOutputStream(out.outputStream().buffered()).use { zos ->
                for (apk in apks) {
                    val name = if (apk.absolutePath == entry.baseApk) "base.apk" else apk.name
                    zos.putNextEntry(ZipEntry(name))
                    FileInputStream(apk).use { ins ->
                        val buf = ByteArray(1 shl 16)
                        while (true) {
                            val n = ins.read(buf); if (n < 0) break
                            zos.write(buf, 0, n); written += n
                            emit(Step.Progress(Stage.UNPACK, written.toFloat() / total, name))
                        }
                    }
                    zos.closeEntry()
                }

                emit(Step.Progress(Stage.HASH, 0f, "scanning for native lib"))
                val lib = Fingerprint.locateLib(entry.apkPaths)
                emit(Step.Progress(Stage.HASH, 1f, lib?.entry ?: "no libroblox.so"))

                emit(Step.Progress(Stage.SEAL, 0.3f, "writing manifest"))
                val manifest = JSONObject().apply {
                    put("package", entry.pkg)
                    put("label", entry.label)
                    put("versionName", entry.versionName)
                    put("versionCode", entry.versionCode)
                    put("splitCount", entry.splitCount)
                    put("apks", JSONArray(apks.map {
                        if (it.absolutePath == entry.baseApk) "base.apk" else it.name
                    }))
                    if (lib != null) put("lib", JSONObject().apply {
                        put("entry", lib.entry)
                        put("size", lib.size)
                        put("sha256", lib.sha256)
                        put("fromApk", lib.fromApk)
                    })
                    put("extractedAt", System.currentTimeMillis())
                }
                zos.putNextEntry(ZipEntry("manifest.json"))
                zos.write(manifest.toString(2).toByteArray())
                zos.closeEntry()

                emit(Step.Progress(Stage.SEAL, 1f, "done"))
                emit(Step.Done(out, lib))
            }
            return out
        } catch (t: Throwable) {
            emit(Step.Failed(t.message ?: t.javaClass.simpleName))
            return null
        }
    }
}
