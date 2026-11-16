package com.workbench.apk.engine

import android.app.AppOpsManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.drawable.Drawable
import android.os.Process
import android.provider.Settings

data class InstalledApp(
    val packageName: String,
    val label: String,
    val icon: Drawable,
    val isSystem: Boolean,
    val sizeBytes: Long,
    val splitCount: Int,
)

object AppScanner {

    /** Third-party, launchable apps, largest first. The icon and label drive the picker. */
    fun scan(context: Context): List<InstalledApp> {
        val pm = context.packageManager
        val self = context.packageName
        val result = ArrayList<InstalledApp>()
        val flags = PackageManager.GET_META_DATA
        for (info in pm.getInstalledApplications(flags)) {
            if (info.packageName == self) continue
            val isSystem = (info.flags and ApplicationInfo.FLAG_SYSTEM) != 0 &&
                (info.flags and ApplicationInfo.FLAG_UPDATED_SYSTEM_APP) == 0
            if (isSystem) continue
            if (pm.getLaunchIntentForPackage(info.packageName) == null) continue
            val base = info.sourceDir?.let { java.io.File(it).length() } ?: 0L
            val splits = info.splitSourceDirs ?: emptyArray()
            val splitBytes = splits.sumOf { java.io.File(it).length() }
            result.add(
                InstalledApp(
                    packageName = info.packageName,
                    label = pm.getApplicationLabel(info).toString(),
                    icon = pm.getApplicationIcon(info),
                    isSystem = false,
                    sizeBytes = base + splitBytes,
                    splitCount = splits.size + 1,
                )
            )
        }
        return result.sortedByDescending { it.sizeBytes }
    }

    /** Paths to the installed base + split APKs. World-readable, no root needed. */
    fun apkPaths(context: Context, pkg: String): List<java.io.File> {
        val info = context.packageManager.getApplicationInfo(pkg, 0)
        val files = ArrayList<java.io.File>()
        info.sourceDir?.let { files.add(java.io.File(it)) }
        info.splitSourceDirs?.forEach { files.add(java.io.File(it)) }
        return files
    }

    /** True once the user has granted usage access (needed to read the last foreground app). */
    fun hasUsageAccess(context: Context): Boolean {
        val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = appOps.unsafeCheckOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS,
            Process.myUid(),
            context.packageName,
        )
        return mode == AppOpsManager.MODE_ALLOWED
    }

    val usageSettingsAction: String get() = Settings.ACTION_USAGE_ACCESS_SETTINGS

    /** The most recently used third-party app, if usage access is granted. */
    fun mostRecentApp(context: Context): String? {
        if (!hasUsageAccess(context)) return null
        val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val now = System.currentTimeMillis()
        val stats = usm.queryUsageStats(
            UsageStatsManager.INTERVAL_DAILY,
            now - 1000L * 60 * 60 * 24,
            now,
        ) ?: return null
        val self = context.packageName
        val pm = context.packageManager
        return stats
            .filter { it.lastTimeUsed > 0 && it.packageName != self }
            .filter { pm.getLaunchIntentForPackage(it.packageName) != null }
            .maxByOrNull { it.lastTimeUsed }
            ?.packageName
    }
}
