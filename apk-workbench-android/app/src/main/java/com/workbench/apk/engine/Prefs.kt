package com.workbench.apk.engine

import android.content.Context
import android.net.Uri

/** Small persistent settings: chosen save folder and the single-zip toggle. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("workbench", Context.MODE_PRIVATE)

    var treeUri: Uri?
        get() = sp.getString(KEY_TREE, null)?.let(Uri::parse)
        set(value) = sp.edit().putString(KEY_TREE, value?.toString()).apply()

    var singleZip: Boolean
        get() = sp.getBoolean(KEY_SINGLE, false)
        set(value) = sp.edit().putBoolean(KEY_SINGLE, value).apply()

    var reMode: Boolean
        get() = sp.getBoolean(KEY_RE, false)
        set(value) = sp.edit().putBoolean(KEY_RE, value).apply()

    private companion object {
        const val KEY_TREE = "tree_uri"
        const val KEY_SINGLE = "single_zip"
        const val KEY_RE = "re_mode"
    }
}
