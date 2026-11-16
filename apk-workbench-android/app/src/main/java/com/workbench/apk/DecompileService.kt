package com.workbench.apk

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import com.workbench.apk.engine.PipelineBus
import com.workbench.apk.engine.Progress
import com.workbench.apk.engine.RunState
import com.workbench.apk.engine.StageStatus
import com.workbench.apk.engine.Workbench
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Runs the decompile as a foreground service with a wake lock, so it keeps going at
 * full CPU while you're in another app (TikTok). It stops only when the work finishes
 * or you close Workbench (swipe it away / force-stop) — see onTaskRemoved.
 */
class DecompileService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val pkg = intent?.getStringExtra(EXTRA_PKG)
        if (pkg == null) { stopSelf(); return START_NOT_STICKY }
        val label = intent.getStringExtra(EXTRA_LABEL) ?: pkg
        val single = intent.getBooleanExtra(EXTRA_SINGLE, false)
        val tree = intent.getStringExtra(EXTRA_TREE)?.let(Uri::parse)

        startForegroundCompat(buildNotification(label, "starting", 0, indeterminate = true))
        acquireWakeLock()
        PipelineBus.state.value = RunState.Running(pkg, label, null)

        scope.launch {
            try {
                Workbench(applicationContext).run(pkg, single, tree).collect { p ->
                    PipelineBus.state.value =
                        if (p.finished) RunState.Done(label, p) else RunState.Running(pkg, label, p)
                    notify(label, p)
                    if (p.finished) finishWork()
                }
            } catch (e: Throwable) {
                PipelineBus.state.value = RunState.Failed(label, e.message ?: "failed")
                finishWork()
            }
        }
        return START_NOT_STICKY
    }

    /** Closing the app from recents stops the decompile, as intended. */
    override fun onTaskRemoved(rootIntent: Intent?) {
        scope.cancel()
        finishWork()
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        scope.cancel()
        releaseWakeLock()
        super.onDestroy()
    }

    private fun finishWork() {
        releaseWakeLock()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) stopForeground(STOP_FOREGROUND_REMOVE)
        else @Suppress("DEPRECATION") stopForeground(true)
        stopSelf()
    }

    // ---- wake lock ----
    private fun acquireWakeLock() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "workbench:decompile").apply {
            setReferenceCounted(false)
            acquire(2 * 60 * 60 * 1000L) // 2h ceiling; released on finish anyway
        }
    }

    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    // ---- notification ----
    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIF_ID, notification)
        }
    }

    private fun notify(label: String, p: Progress) {
        val stage = p.stages.firstOrNull { it.status == StageStatus.RUNNING }?.stage?.label
            ?: if (p.finished) "done" else "working"
        val pct = overallPercent(p)
        val n = buildNotification(label, stage, pct, indeterminate = pct == 0 && !p.finished)
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).notify(NOTIF_ID, n)
    }

    private fun buildNotification(label: String, stage: String, pct: Int, indeterminate: Boolean): Notification {
        ensureChannel()
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            Notification.Builder(this, CHANNEL_ID) else @Suppress("DEPRECATION") Notification.Builder(this)
        return builder
            .setContentTitle("Decompiling $label")
            .setContentText(if (pct > 0) "$stage · $pct%" else stage)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setProgress(100, pct, indeterminate)
            .setContentIntent(open)
            .build()
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val mgr = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (mgr.getNotificationChannel(CHANNEL_ID) == null) {
            mgr.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Decompile", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "Running decompile in the background"
                    setShowBadge(false)
                }
            )
        }
    }

    companion object {
        private const val NOTIF_ID = 42
        private const val CHANNEL_ID = "decompile"
        const val EXTRA_PKG = "pkg"
        const val EXTRA_LABEL = "label"
        const val EXTRA_SINGLE = "single"
        const val EXTRA_TREE = "tree"

        fun overallPercent(p: Progress): Int {
            val done = p.stages.count { it.status == StageStatus.DONE }
            val active = p.stages.firstOrNull { it.status == StageStatus.RUNNING }?.progress ?: 0f
            return (((done + active) / p.stages.size) * 100).toInt().coerceIn(0, 100)
        }

        fun start(context: Context, pkg: String, label: String, single: Boolean, tree: Uri?) {
            val intent = Intent(context, DecompileService::class.java).apply {
                putExtra(EXTRA_PKG, pkg)
                putExtra(EXTRA_LABEL, label)
                putExtra(EXTRA_SINGLE, single)
                putExtra(EXTRA_TREE, tree?.toString())
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent)
            else context.startService(intent)
        }
    }
}
