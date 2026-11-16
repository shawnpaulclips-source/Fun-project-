package com.workbench.apk.engine

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.documentfile.provider.DocumentFile
import java.io.File

/**
 * Writes the finished zip part(s) to where the user wants them:
 *   - a folder they picked (Storage Access Framework tree Uri), or
 *   - Downloads/apk-workbench/<pkg> by default (MediaStore on Q+, direct file below).
 */
object Output {

    fun publish(
        context: Context,
        treeUri: Uri?,
        pkg: String,
        files: List<File>,
        onProgress: (Float) -> Unit = {},
    ): String {
        val sub = "apk-workbench/${pkg.replace('.', '_')}"
        return if (treeUri != null) publishTree(context, treeUri, sub, files, onProgress)
        else publishDownloads(context, sub, files, onProgress)
    }

    private fun publishTree(
        context: Context, treeUri: Uri, sub: String, files: List<File>, onProgress: (Float) -> Unit,
    ): String {
        val root = DocumentFile.fromTreeUri(context, treeUri)
            ?: throw IllegalStateException("can't open the chosen folder")
        var dir = root
        for (part in sub.split('/')) {
            dir = dir.findFile(part)?.takeIf { it.isDirectory } ?: dir.createDirectory(part)
                ?: throw IllegalStateException("can't create $part in the chosen folder")
        }
        files.forEachIndexed { i, f ->
            dir.findFile(f.name)?.delete()
            val doc = dir.createFile("application/zip", f.name)
                ?: throw IllegalStateException("can't create ${f.name} in the chosen folder")
            context.contentResolver.openOutputStream(doc.uri).use { os ->
                f.inputStream().use { it.copyTo(os!!, 1 shl 20) }
            }
            onProgress((i + 1f) / files.size)
        }
        val label = root.name ?: "chosen folder"
        return "$label/$sub"
    }

    private fun publishDownloads(
        context: Context, sub: String, files: List<File>, onProgress: (Float) -> Unit,
    ): String {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val resolver = context.contentResolver
            files.forEachIndexed { i, f ->
                resolver.delete(
                    MediaStore.Downloads.EXTERNAL_CONTENT_URI,
                    "${MediaStore.Downloads.RELATIVE_PATH}=? AND ${MediaStore.Downloads.DISPLAY_NAME}=?",
                    arrayOf("${Environment.DIRECTORY_DOWNLOADS}/$sub/", f.name),
                )
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, f.name)
                    put(MediaStore.Downloads.MIME_TYPE, "application/zip")
                    put(MediaStore.Downloads.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/$sub")
                    put(MediaStore.Downloads.IS_PENDING, 1)
                }
                val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: throw IllegalStateException("could not create ${f.name} in Downloads")
                resolver.openOutputStream(uri).use { os -> f.inputStream().use { it.copyTo(os!!, 1 shl 20) } }
                resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
                onProgress((i + 1f) / files.size)
            }
        } else {
            @Suppress("DEPRECATION")
            val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), sub)
                .apply { mkdirs() }
            files.forEachIndexed { i, f -> f.copyTo(File(dir, f.name), overwrite = true); onProgress((i + 1f) / files.size) }
        }
        return "Downloads/$sub"
    }
}
