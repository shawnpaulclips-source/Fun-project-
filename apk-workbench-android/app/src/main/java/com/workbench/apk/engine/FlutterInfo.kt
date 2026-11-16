package com.workbench.apk.engine

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Pulls the facts blutter needs straight out of a Flutter app, with no toolchain:
 *  - the Dart/Flutter SDK version from libflutter.so
 *  - the AOT snapshot build id from libapp.so (the _kDartSnapshotBuildId symbol)
 * Pure Kotlin, runs on the phone. Reads ELF64 little-endian only (arm64 Android).
 */
object FlutterInfo {

    data class Report(
        val dartVersion: String?,
        val snapshotBuildId: String?,
        val notes: List<String>,
    )

    private val versionRe = Regex("""(\d+\.\d+\.\d+) \((stable|beta|dev|master)\)""")

    fun inspect(libDir: File): Report {
        val notes = mutableListOf<String>()
        val flutter = File(libDir, "libflutter.so")
        val app = File(libDir, "libapp.so")

        val version = if (flutter.isFile) {
            findVersion(flutter).also { if (it == null) notes += "no SDK version string found in libflutter.so" }
        } else {
            notes += "libflutter.so not found"
            null
        }

        val build = if (app.isFile) {
            val sym = readSymbolString(app, "_kDartSnapshotBuildId")
            if (sym != null && hex32Full.matches(sym)) {
                sym
            } else {
                // Some builds alias the symbol into the GNU build-id note, so its bytes aren't
                // the hex id. Fall back to the 32-hex run the snapshot repeats (the id is written
                // into the snapshot header more than once). Reported as a candidate, not a fact.
                notes += "_kDartSnapshotBuildId points at a non-hex section; build id below is a candidate " +
                    "(the most repeated 32-hex run in libapp.so)"
                repeatedHex(app)
            }
        } else {
            notes += "libapp.so not found"
            null
        }

        return Report(version, build, notes)
    }

    private val hex32Full = Regex("[0-9a-f]{32}")
    private val hex32Any = Regex("(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")

    private fun repeatedHex(file: File): String? = runCatching {
        val text = String(file.readBytes(), Charsets.ISO_8859_1)
        val counts = hex32Any.findAll(text).map { it.value }.groupingBy { it }.eachCount()
        counts.entries.filter { it.value >= 2 }.maxByOrNull { it.value }?.key
            ?: counts.keys.firstOrNull()
    }.getOrNull()

    private fun findVersion(file: File): String? = runCatching {
        // Version string is ASCII inside the binary; scan the bytes as Latin-1.
        val text = String(file.readBytes(), Charsets.ISO_8859_1)
        versionRe.find(text)?.let { "${it.groupValues[1]} (${it.groupValues[2]})" }
    }.getOrNull()

    private fun readSymbolString(file: File, name: String): String? = runCatching {
        val raw = file.readBytes()
        val buf = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN)
        if (raw.size < 64 || buf.getInt(0) != 0x464C457F) return@runCatching null   // \x7FELF
        if (raw[4].toInt() != 2) return@runCatching null                              // ELF64 only

        val phoff = buf.getLong(0x20).toInt()
        val shoff = buf.getLong(0x28).toInt()
        val phentsize = buf.getShort(0x36).toInt() and 0xFFFF
        val phnum = buf.getShort(0x38).toInt() and 0xFFFF
        val shentsize = buf.getShort(0x3A).toInt() and 0xFFFF
        val shnum = buf.getShort(0x3C).toInt() and 0xFFFF

        // PT_LOAD segments map virtual addresses to file offsets.
        val loads = (0 until phnum).mapNotNull { i ->
            val p = phoff + i * phentsize
            if (buf.getInt(p) != 1) return@mapNotNull null          // PT_LOAD
            Triple(buf.getLong(p + 16), buf.getLong(p + 8), buf.getLong(p + 32)) // vaddr, offset, filesz
        }

        for (i in 0 until shnum) {
            val s = shoff + i * shentsize
            if (buf.getInt(s + 4) != 11) continue                    // SHT_DYNSYM
            val symOff = buf.getLong(s + 24).toInt()
            val symSize = buf.getLong(s + 32).toInt()
            val strS = shoff + buf.getInt(s + 40) * shentsize        // sh_link -> string table
            val strOff = buf.getLong(strS + 24).toInt()
            val strSize = buf.getLong(strS + 32).toInt()

            for (e in 0 until symSize / 24) {
                val sym = symOff + e * 24
                val nameIdx = buf.getInt(sym)
                val value = buf.getLong(sym + 8)
                val size = buf.getLong(sym + 16)
                if (size <= 0) continue
                if (cstr(raw, strOff + nameIdx, strOff + strSize) != name) continue

                val seg = loads.firstOrNull { value >= it.first && value < it.first + it.third } ?: return@runCatching null
                val fileOff = (value - seg.first + seg.second).toInt()
                val len = size.toInt().coerceAtMost(raw.size - fileOff)
                return@runCatching String(raw, fileOff, len, Charsets.ISO_8859_1).trimEnd('\u0000')
            }
        }
        null
    }.getOrNull()

    private fun cstr(raw: ByteArray, start: Int, limit: Int): String {
        var end = start
        while (end < limit && end < raw.size && raw[end] != 0.toByte()) end++
        return String(raw, start, end - start, Charsets.ISO_8859_1)
    }
}
