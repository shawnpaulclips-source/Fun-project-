package com.marginalia.loader

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

enum class Stage(val label: String) {
    READ("Reading package"),
    UNPACK("Packing payload"),
    HASH("Hashing libroblox.so"),
    SEAL("Sealing zip + manifest"),
    SPLIT("Splitting into parts");

    companion object { val ordered = entries.toList() }
}

sealed class Step {
    data class Progress(val stage: Stage, val stageFraction: Float, val note: String) : Step()
    data class Done(val parts: List<File>, val manifest: File?, val lib: LibInfo?) : Step()
    data class Failed(val reason: String) : Step()
}

object Extractor {

    private const val TARGET_LIB = "lib/arm64-v8a/libroblox.so"

    /**
     * Build the payload zip, then slice it into parts of at most [maxBytes] so each
     * piece clears a chat/host attachment cap. [libOnly] packs just libroblox.so —
     * the only file the offset dump needs — which keeps the part count tiny.
     *
     * Reassemble on the far side with: cat <name>.* > <name>  (order is numeric).
     */
    fun extract(
        context: Context,
        entry: AppEntry,
        maxBytes: Long,
        libOnly: Boolean,
        emit: (Step) -> Unit,
    ): List<File> {
        val dir = File(context.getExternalFilesDir(null), "bundles").apply { mkdirs() }
        // wipe prior output so old parts never mix with new ones
        dir.listFiles()?.forEach { it.delete() }

        val stem = if (libOnly) "${entry.pkg}-${entry.versionCode}-libroblox"
        else "${entry.pkg}-${entry.versionCode}-bundle"
        val zip = File(dir, "$stem.zip")

        try {
            emit(Step.Progress(Stage.READ, 0f, "locating apks"))
            val apks = entry.apkPaths.map(::File).filter { it.exists() }
            if (apks.isEmpty()) { emit(Step.Failed("no readable apk paths")); return emptyList() }
            emit(Step.Progress(Stage.READ, 1f, "${apks.size} apk(s)"))

            val lib = Fingerprint.locateLib(entry.apkPaths)
            if (libOnly && lib == null) {
                emit(Step.Failed("no libroblox.so in this app — turn off 'lib only' to grab the full bundle"))
                return emptyList()
            }

            ZipOutputStream(zip.outputStream().buffered()).use { zos ->
                if (libOnly) {
                    packLibOnly(entry, lib!!, zos, emit)
                } else {
                    packFullBundle(entry, apks, zos, emit)
                }

                emit(Step.Progress(Stage.HASH, 1f, lib?.entry ?: "no libroblox.so"))

                emit(Step.Progress(Stage.SEAL, 0.4f, "writing manifest"))
                val inner = JSONObject().apply {
                    put("package", entry.pkg)
                    put("label", entry.label)
                    put("versionName", entry.versionName)
                    put("versionCode", entry.versionCode)
                    put("mode", if (libOnly) "libroblox.so only" else "full bundle")
                    if (!libOnly) put("apks", JSONArray(apks.map {
                        if (it.absolutePath == entry.baseApk) "base.apk" else it.name
                    }))
                    if (lib != null) put("lib", JSONObject().apply {
                        put("entry", lib.entry)
                        put("size", lib.size)
                        put("sha256", lib.sha256)
                    })
                    put("extractedAt", System.currentTimeMillis())
                }
                zos.putNextEntry(ZipEntry("manifest.json"))
                zos.write(inner.toString(2).toByteArray())
                zos.closeEntry()
                emit(Step.Progress(Stage.SEAL, 1f, "zip sealed"))
            }

            val (parts, manifest) = split(zip, maxBytes, emit)
            zip.delete()
            emit(Step.Done(parts, manifest, lib))
            return parts
        } catch (t: Throwable) {
            emit(Step.Failed(t.message ?: t.javaClass.simpleName))
            return emptyList()
        }
    }

    private fun packLibOnly(entry: AppEntry, lib: LibInfo, zos: ZipOutputStream, emit: (Step) -> Unit) {
        val holder = entry.apkPaths.map(::File).first { apk ->
            apk.exists() && ZipFile(apk).use { it.getEntry(lib.entry) != null }
        }
        ZipFile(holder).use { zf ->
            val e = zf.getEntry(lib.entry)
            val total = e.size.coerceAtLeast(1)
            var written = 0L
            zos.putNextEntry(ZipEntry(lib.entry))
            zf.getInputStream(e).use { ins ->
                val buf = ByteArray(1 shl 16)
                while (true) {
                    val n = ins.read(buf); if (n < 0) break
                    zos.write(buf, 0, n); written += n
                    emit(Step.Progress(Stage.UNPACK, written.toFloat() / total, "libroblox.so"))
                }
            }
            zos.closeEntry()
        }
    }

    private fun packFullBundle(entry: AppEntry, apks: List<File>, zos: ZipOutputStream, emit: (Step) -> Unit) {
        val total = apks.sumOf { it.length() }.coerceAtLeast(1)
        var written = 0L
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
    }

    /** Slice [src] into src.name + ".001", ".002"… each <= maxBytes; write a parts manifest. */
    private fun split(src: File, maxBytes: Long, emit: (Step) -> Unit): Pair<List<File>, File> {
        val dir = src.parentFile!!
        val total = src.length()
        val count = ((total + maxBytes - 1) / maxBytes).toInt().coerceAtLeast(1)
        val md = MessageDigest.getInstance("SHA-256")
        val parts = ArrayList<File>(count)

        FileInputStream(src).use { ins ->
            val buf = ByteArray(1 shl 16)
            var written = 0L
            for (idx in 1..count) {
                val part = File(dir, "%s.%03d".format(src.name, idx))
                part.outputStream().buffered().use { out ->
                    var inPart = 0L
                    while (inPart < maxBytes) {
                        val want = minOf(buf.size.toLong(), maxBytes - inPart).toInt()
                        val n = ins.read(buf, 0, want); if (n < 0) break
                        out.write(buf, 0, n); md.update(buf, 0, n)
                        inPart += n; written += n
                        emit(Step.Progress(Stage.SPLIT, written.toFloat() / total, "part $idx/$count"))
                    }
                }
                parts += part
            }
        }

        val sha = md.digest().joinToString("") { "%02x".format(it) }
        val manifest = File(dir, "${src.name}.parts.json")
        manifest.writeText(JSONObject().apply {
            put("name", src.name)
            put("parts", count)
            put("totalSize", total)
            put("maxBytes", maxBytes)
            put("sha256", sha)
            put("reassemble", "cat ${src.name}.* > ${src.name}  # then verify sha256, then unzip")
            put("order", JSONArray(parts.map { it.name }))
        }.toString(2))
        parts += manifest
        return parts to manifest
    }
}
