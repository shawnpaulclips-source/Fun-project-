package com.marginalia.loader

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import java.io.File

object Share {
    fun send(context: Context, files: List<File>, label: String) {
        val auth = "${context.packageName}.files"
        if (files.size == 1) {
            val uri = FileProvider.getUriForFile(context, auth, files.first())
            val i = Intent(Intent.ACTION_SEND).apply {
                type = "application/zip"
                putExtra(Intent.EXTRA_STREAM, uri)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            context.startActivity(Intent.createChooser(i, label).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } else {
            val uris = ArrayList(files.map { FileProvider.getUriForFile(context, auth, it) })
            val i = Intent(Intent.ACTION_SEND_MULTIPLE).apply {
                type = "application/vnd.android.package-archive"
                putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            context.startActivity(Intent.createChooser(i, label).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }
}
