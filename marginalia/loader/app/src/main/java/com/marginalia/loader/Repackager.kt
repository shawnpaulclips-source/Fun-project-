package com.marginalia.loader

import android.content.Context
import android.net.Uri
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

sealed class RepackStep {
    data class Progress(val fraction: Float, val note: String) : RepackStep()
    data class Done(val signedApks: List<File>) : RepackStep()
    data class Failed(val reason: String) : RepackStep()
}

/**
 * Rebuild a bundle's split(s): swap lib/arm64-v8a/libroblox.so for a replacement,
 * then sign each apk so the set is installable with `adb install-multiple` /
 * a split-aware package installer.
 *
 * The native lib is stored uncompressed (STORED) so the loader can mmap it;
 * apksig performs the signing-scheme alignment on output.
 */
object Repackager {

    private const val TARGET = "lib/arm64-v8a/libroblox.so"

    fun rebuild(
        context: Context,
        bundleZip: Uri,
        newLib: Uri,
        emit: (RepackStep) -> Unit,
    ): List<File> {
        val work = File(context.getExternalFilesDir(null), "out").apply { mkdirs() }
        work.listFiles()?.forEach { it.delete() }

        try {
            emit(RepackStep.Progress(0.02f, "reading bundle"))
            val localBundle = File(context.cacheDir, "bundle.zip")
            context.contentResolver.openInputStream(bundleZip)!!.use { ins ->
                localBundle.outputStream().use { ins.copyTo(it) }
            }
            val libBytes = context.contentResolver.openInputStream(newLib)!!.use { it.readBytes() }

            val apkEntries = ArrayList<String>()
            ZipFile(localBundle).use { z ->
                val e = z.entries()
                while (e.hasMoreElements()) {
                    val name = e.nextElement().name
                    if (name.endsWith(".apk")) apkEntries += name
                }
            }
            if (apkEntries.isEmpty()) { emit(RepackStep.Failed("bundle has no apks")); return emptyList() }

            val signed = ArrayList<File>()

            ZipFile(localBundle).use { bundle ->
                for ((i, apkName) in apkEntries.withIndex()) {
                    val base = apkName.substringAfterLast('/')
                    emit(RepackStep.Progress(0.1f + 0.7f * i / apkEntries.size, "rebuilding $base"))

                    val rawApk = File(context.cacheDir, base)
                    bundle.getInputStream(bundle.getEntry(apkName)).use { ins ->
                        rawApk.outputStream().use { ins.copyTo(it) }
                    }

                    val rebuilt = File(context.cacheDir, "rebuilt-$base")
                    val swapped = swapLib(rawApk, rebuilt, libBytes)
                    rawApk.delete()

                    val outApk = File(work, base)
                    if (swapped) {
                        emit(RepackStep.Progress(0.1f + 0.7f * i / apkEntries.size, "signing $base (lib swapped)"))
                        Signer.sign(context, rebuilt, outApk)
                    } else {
                        // this split didn't carry the lib — resign it unchanged so the set stays consistent
                        emit(RepackStep.Progress(0.1f + 0.7f * i / apkEntries.size, "signing $base"))
                        Signer.sign(context, rebuilt, outApk)
                    }
                    rebuilt.delete()
                    signed += outApk
                }
            }

            localBundle.delete()
            emit(RepackStep.Progress(1f, "done"))
            emit(RepackStep.Done(signed))
            return signed
        } catch (t: Throwable) {
            emit(RepackStep.Failed(t.message ?: t.javaClass.simpleName))
            return emptyList()
        }
    }

    /** Copy [inApk] to [outApk], replacing the TARGET entry with [libBytes]. Returns true if swapped. */
    private fun swapLib(inApk: File, outApk: File, libBytes: ByteArray): Boolean {
        var swapped = false
        ZipFile(inApk).use { zin ->
            ZipOutputStream(outApk.outputStream().buffered()).use { zout ->
                val e = zin.entries()
                while (e.hasMoreElements()) {
                    val src = e.nextElement()
                    if (src.name == "META-INF/MANIFEST.MF" ||
                        src.name.startsWith("META-INF/") &&
                        (src.name.endsWith(".SF") || src.name.endsWith(".RSA") || src.name.endsWith(".EC"))
                    ) continue // drop old v1 signature; apksig rewrites it

                    if (src.name == TARGET) {
                        val entry = ZipEntry(TARGET).apply {
                            method = ZipEntry.STORED
                            size = libBytes.size.toLong()
                            compressedSize = libBytes.size.toLong()
                            crc = java.util.zip.CRC32().apply { update(libBytes) }.value
                        }
                        zout.putNextEntry(entry)
                        zout.write(libBytes)
                        zout.closeEntry()
                        swapped = true
                    } else {
                        val entry = ZipEntry(src.name).apply {
                            method = src.method
                            if (src.method == ZipEntry.STORED) {
                                size = src.size
                                compressedSize = src.compressedSize
                                crc = src.crc
                            }
                        }
                        zout.putNextEntry(entry)
                        zin.getInputStream(src).use { it.copyTo(zout) }
                        zout.closeEntry()
                    }
                }
            }
        }
        return swapped
    }
}
