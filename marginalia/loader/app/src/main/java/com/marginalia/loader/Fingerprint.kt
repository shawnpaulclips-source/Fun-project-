package com.marginalia.loader

import java.io.File
import java.security.MessageDigest
import java.util.zip.ZipFile

data class LibInfo(val entry: String, val size: Long, val sha256: String, val fromApk: String)

object Fingerprint {

    private val LIBS = listOf(
        "lib/arm64-v8a/libroblox.so",
        "lib/armeabi-v7a/libroblox.so",
    )

    /** Find the native client lib across the bundle's apks and hash it. */
    fun locateLib(apks: List<String>): LibInfo? {
        for (path in apks) {
            val f = File(path)
            if (!f.exists()) continue
            ZipFile(f).use { zip ->
                for (name in LIBS) {
                    val e = zip.getEntry(name) ?: continue
                    val md = MessageDigest.getInstance("SHA-256")
                    zip.getInputStream(e).use { ins ->
                        val buf = ByteArray(1 shl 16)
                        while (true) {
                            val n = ins.read(buf); if (n < 0) break; md.update(buf, 0, n)
                        }
                    }
                    return LibInfo(
                        entry = name,
                        size = e.size,
                        sha256 = md.digest().joinToString("") { "%02x".format(it) },
                        fromApk = f.name,
                    )
                }
            }
        }
        return null
    }
}
