package com.workbench.apk.engine

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.zip.Deflater
import java.util.zip.DeflaterOutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

/**
 * Splits a folder tree into zip parts each under the 30 MB upload cap, cutting any
 * single file bigger than the chunk size into raw `.partNNN` pieces. Port of the
 * PC tool's packer, which round-trips 1:1 (verified against sha256 on reassembly).
 */
object Packer {

    const val PART_LIMIT = 25_000_000L       // target bytes per part
    const val HARD_PART_CAP = 30_000_000L    // any part over this is a bug
    const val CHUNK_SIZE = 20_000_000L       // files above this are chunked
    const val PART_HEADROOM = 256_000L       // reserved in part 01 for the manifest
    const val ENTRY_OVERHEAD = 300L
    const val MANIFEST = "MANIFEST.json"
    const val README = "REASSEMBLE.txt"

    private val README_TEXT = """
        These are split parts of one decompiled app.

        Put every *_part*.zip in one folder and either run the PC tool's
        `workbench.py unpack`, or unzip them all into one folder and read MANIFEST.json.

        Files over the chunk size were cut into <name>.partNNN pieces. MANIFEST.json
        lists each with its ordered pieces and a sha256. Concatenate the pieces in
        order into the original path, then check the sha256.
    """.trimIndent()

    private data class Entry(val arc: String, val source: File, val offset: Long, val length: Long)

    fun sha256(file: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(1 shl 20)
            while (true) {
                val n = input.read(buf); if (n < 0) break
                md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    /** Deflated size of a byte range, so part planning matches the real compressed output. */
    private fun deflatedSize(file: File, offset: Long, length: Long): Long {
        val def = Deflater(6, true)
        val out = object : java.io.OutputStream() {
            var count = 0L
            override fun write(b: Int) { count++ }
            override fun write(b: ByteArray, off: Int, len: Int) { count += len }
        }
        DeflaterOutputStream(out, def, 1 shl 16).use { dos ->
            file.inputStream().use { input ->
                input.skip(offset)
                var remaining = length
                val buf = ByteArray(1 shl 20)
                while (remaining > 0) {
                    val want = minOf(buf.size.toLong(), remaining).toInt()
                    val n = input.read(buf, 0, want); if (n < 0) break
                    remaining -= n
                    dos.write(buf, 0, n)
                }
            }
        }
        return out.count
    }

    /**
     * @param onProgress fraction 0..1 as parts are written.
     * @return the written part files in order.
     */
    fun pack(
        src: File,
        dest: File,
        name: String,
        meta: JSONObject,
        partLimit: Long = PART_LIMIT,
        chunkSize: Long = CHUNK_SIZE,
        onProgress: (Float) -> Unit = {},
    ): List<File> {
        dest.mkdirs()
        dest.listFiles { f -> f.name.startsWith("${name}_part") && f.name.endsWith(".zip") }
            ?.forEach { it.delete() }

        val entries = ArrayList<Entry>()
        val chunked = JSONArray()
        src.walkTopDown().filter { it.isFile }.sortedBy { it.path }.forEach { f ->
            val rel = f.relativeTo(src).path.replace(File.separatorChar, '/')
            val size = f.length()
            if (size <= chunkSize) {
                entries.add(Entry(rel, f, 0, size))
            } else {
                val pieces = JSONArray()
                var i = 0
                var off = 0L
                while (off < size) {
                    val arc = "%s.part%03d".format(rel, i)
                    entries.add(Entry(arc, f, off, minOf(chunkSize, size - off)))
                    pieces.put(arc); i++; off += chunkSize
                }
                chunked.put(JSONObject().apply {
                    put("path", rel); put("size", size); put("sha256", sha256(f)); put("pieces", pieces)
                })
            }
        }

        // Greedy bin-pack by estimated compressed cost.
        val groups = ArrayList<MutableList<Entry>>()
        var cur = ArrayList<Entry>()
        var used = PART_HEADROOM
        for (e in entries) {
            val cost = (deflatedSize(e.source, e.offset, e.length) * 1.01).toLong() + ENTRY_OVERHEAD
            if (cost > partLimit) throw IllegalStateException("${e.arc} alone exceeds the part limit")
            if (cur.isNotEmpty() && used + cost > partLimit) { groups.add(cur); cur = ArrayList(); used = 0 }
            cur.add(e); used += cost
        }
        groups.add(cur)

        val partNames = (1..groups.size).map { "%s_part%02d.zip".format(name, it) }
        val manifest = JSONObject(meta.toString()).apply {
            put("name", name)
            put("parts", JSONArray(partNames))
            put("chunked", chunked)
        }
        val manifestBytes = manifest.toString().toByteArray(Charsets.UTF_8)
        if (manifestBytes.size > PART_HEADROOM) throw IllegalStateException("manifest larger than reserved space")

        val written = ArrayList<File>()
        groups.forEachIndexed { i, group ->
            val target = File(dest, partNames[i])
            ZipOutputStream(target.outputStream().buffered()).use { zos ->
                zos.setLevel(6)
                if (i == 0) {
                    zos.putEntry(MANIFEST, manifestBytes)
                    zos.putEntry(README, README_TEXT.toByteArray(Charsets.UTF_8))
                }
                for (e in group) {
                    zos.putNextEntry(ZipEntry(e.arc))
                    e.source.inputStream().use { input ->
                        input.skip(e.offset)
                        var remaining = e.length
                        val buf = ByteArray(1 shl 20)
                        while (remaining > 0) {
                            val want = minOf(buf.size.toLong(), remaining).toInt()
                            val n = input.read(buf, 0, want); if (n < 0) break
                            remaining -= n
                            zos.write(buf, 0, n)
                        }
                    }
                    zos.closeEntry()
                }
            }
            val size = target.length()
            if (size > HARD_PART_CAP) throw IllegalStateException("${target.name} is $size bytes, over the cap")
            written.add(target)
            onProgress((i + 1).toFloat() / groups.size)
        }
        return written
    }

    private fun ZipOutputStream.putEntry(name: String, bytes: ByteArray) {
        putNextEntry(ZipEntry(name)); write(bytes); closeEntry()
    }

    /**
     * The whole tree in one zip, no size cap. For keeping or USB transfer — too big to
     * upload back through chat, which is what the split [pack] is for.
     */
    fun packSingle(
        src: File,
        dest: File,
        name: String,
        meta: JSONObject,
        onProgress: (Float) -> Unit = {},
    ): List<File> {
        dest.mkdirs()
        val target = File(dest, "$name.zip")
        if (target.exists()) target.delete()
        val all = src.walkTopDown().filter { it.isFile }.sortedBy { it.path }.toList()
        ZipOutputStream(target.outputStream().buffered()).use { zos ->
            zos.setLevel(6)
            zos.putEntry(MANIFEST, JSONObject(meta.toString()).apply { put("name", name); put("single", true) }
                .toString().toByteArray(Charsets.UTF_8))
            all.forEachIndexed { i, f ->
                val rel = f.relativeTo(src).path.replace(File.separatorChar, '/')
                zos.putNextEntry(ZipEntry(rel))
                f.inputStream().use { it.copyTo(zos, 1 shl 20) }
                zos.closeEntry()
                if (i % 32 == 0) onProgress((i + 1f) / all.size)
            }
        }
        onProgress(1f)
        return listOf(target)
    }

    /** Reassemble parts into a tree and verify every chunked file's sha256. */
    fun unpack(partsDir: File, out: File) {
        val zips = partsDir.listFiles { f -> f.name.contains("_part") && f.name.endsWith(".zip") }
            ?.sortedBy { it.name } ?: throw IllegalStateException("no parts found")
        val staging = File(out.parentFile, "${out.name}.staging").apply { mkdirs() }
        try {
            for (z in zips) ZipFile(z).use { zf ->
                val en = zf.entries()
                while (en.hasMoreElements()) {
                    val entry = en.nextElement()
                    val dst = safeChild(staging, entry.name)
                    if (entry.isDirectory) { dst.mkdirs(); continue }
                    dst.parentFile?.mkdirs()
                    zf.getInputStream(entry).use { it.copyTo(dst.outputStream()) }
                }
            }
            val manifest = JSONObject(File(staging, MANIFEST).readText())
            val chunked = manifest.getJSONArray("chunked")
            val pieceSet = HashSet<String>()
            for (i in 0 until chunked.length()) {
                val pieces = chunked.getJSONObject(i).getJSONArray("pieces")
                for (j in 0 until pieces.length()) pieceSet.add(pieces.getString(j))
            }
            out.mkdirs()
            staging.walkTopDown().filter { it.isFile }.forEach { f ->
                val rel = f.relativeTo(staging).path.replace(File.separatorChar, '/')
                if (rel == MANIFEST || rel == README || rel in pieceSet) return@forEach
                val dst = safeChild(out, rel); dst.parentFile?.mkdirs(); f.copyTo(dst, overwrite = true)
            }
            for (i in 0 until chunked.length()) {
                val c = chunked.getJSONObject(i)
                val dst = safeChild(out, c.getString("path")); dst.parentFile?.mkdirs()
                dst.outputStream().use { o ->
                    val pieces = c.getJSONArray("pieces")
                    for (j in 0 until pieces.length()) File(staging, pieces.getString(j)).inputStream().use { it.copyTo(o) }
                }
                if (sha256(dst) != c.getString("sha256")) throw IllegalStateException("checksum mismatch: ${c.getString("path")}")
            }
        } finally {
            staging.deleteRecursively()
        }
    }

    private fun safeChild(root: File, rel: String): File {
        val child = File(root, rel).canonicalFile
        if (!child.path.startsWith(root.canonicalFile.path + File.separator) && child != root.canonicalFile)
            throw IllegalStateException("unsafe path in archive: $rel")
        return child
    }
}
