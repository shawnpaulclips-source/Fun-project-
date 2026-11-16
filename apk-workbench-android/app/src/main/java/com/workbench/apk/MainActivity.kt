package com.workbench.apk

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Surface
import android.os.Build
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.workbench.apk.engine.PipelineBus
import com.workbench.apk.engine.Prefs
import com.workbench.apk.engine.RunState
import com.workbench.apk.ui.DoneScreen
import com.workbench.apk.ui.PickerScreen
import com.workbench.apk.ui.PipelineScreen
import com.workbench.apk.ui.WorkbenchTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { WorkbenchTheme { Surface(Modifier.fillMaxSize()) { Root() } } }
    }
}

@Composable
private fun Root() {
    val context = LocalContext.current
    val prefs = remember { Prefs(context) }
    val run by PipelineBus.state.collectAsState()
    var singleZip by remember { mutableStateOf(prefs.singleZip) }
    var treeUri by remember { mutableStateOf(prefs.treeUri) }
    var folderName by remember { mutableStateOf(treeUri?.let { folderLabel(it) }) }

    val pickFolder = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocumentTree(),
    ) { uri ->
        if (uri != null) {
            // Keep write access across restarts.
            context.contentResolver.takePersistableUriPermission(
                uri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
            )
            prefs.treeUri = uri
            treeUri = uri
            folderName = folderLabel(uri)
        }
    }

    // Notification permission (Android 13+) so the background-progress notification shows.
    val askNotif = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }

    fun launch(app: com.workbench.apk.engine.InstalledApp) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            askNotif.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }
        DecompileService.start(context, app.packageName, app.label, singleZip, treeUri)
    }

    AnimatedContent(
        targetState = run,
        transitionSpec = {
            (fadeIn() togetherWith fadeOut()).using(SizeTransform(clip = false))
        },
        label = "screen",
    ) { s ->
        when (s) {
            is RunState.Idle -> PickerScreen(
                modifier = Modifier.padding(),
                folderName = folderName,
                singleZip = singleZip,
                onPickFolder = { pickFolder.launch(null) },
                onUseDownloads = { prefs.treeUri = null; treeUri = null; folderName = null },
                onToggleSingle = { singleZip = it; prefs.singleZip = it },
                onPick = { app -> launch(app) },
            )
            is RunState.Running -> PipelineScreen(label = s.label, progress = s.progress)
            is RunState.Done -> DoneScreen(
                label = s.label,
                progress = s.progress,
                onBack = { PipelineBus.state.value = RunState.Idle },
            )
            is RunState.Failed -> PipelineScreen(label = s.label, progress = null)
        }
    }
}

/** A readable folder name out of a tree Uri (the last path segment after the ':'). */
private fun folderLabel(uri: Uri): String {
    val last = uri.lastPathSegment ?: return "chosen folder"
    return last.substringAfterLast(':').substringAfterLast('/').ifBlank { last }
}
