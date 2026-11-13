package com.marginalia.loader

import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.drawable.Drawable
import java.io.File

data class AppEntry(
    val label: String,
    val pkg: String,
    val versionName: String,
    val versionCode: Long,
    val baseApk: String,
    val splits: List<String>,
    val icon: Drawable,
    val totalSize: Long,
    val isSystem: Boolean,
) {
    val apkPaths: List<String> get() = buildList { add(baseApk); addAll(splits) }
    val splitCount: Int get() = splits.size
}

object AppScanner {

    fun scan(context: Context): List<AppEntry> {
        val pm = context.packageManager
        val out = ArrayList<AppEntry>()

        @Suppress("DEPRECATION")
        val packages = pm.getInstalledPackages(0)

        for (pi in packages) {
            val ai: ApplicationInfo = pi.applicationInfo ?: continue
            val base = ai.sourceDir ?: continue
            val splits = ai.splitSourceDirs?.toList() ?: emptyList()

            val code = if (android.os.Build.VERSION.SDK_INT >= 28)
                pi.longVersionCode else @Suppress("DEPRECATION") pi.versionCode.toLong()

            var size = 0L
            for (p in buildList { add(base); addAll(splits) }) {
                size += runCatching { File(p).length() }.getOrDefault(0L)
            }

            out += AppEntry(
                label = runCatching { pm.getApplicationLabel(ai).toString() }.getOrDefault(pi.packageName),
                pkg = pi.packageName,
                versionName = pi.versionName ?: "?",
                versionCode = code,
                baseApk = base,
                splits = splits,
                icon = runCatching { pm.getApplicationIcon(ai) }
                    .getOrElse { pm.defaultActivityIcon },
                totalSize = size,
                isSystem = (ai.flags and ApplicationInfo.FLAG_SYSTEM) != 0,
            )
        }

        // user apps first, then by label; Roblox floated to the very top if present
        return out.sortedWith(
            compareByDescending<AppEntry> { it.pkg.contains("roblox", true) }
                .thenBy { it.isSystem }
                .thenBy { it.label.lowercase() }
        )
    }
}
