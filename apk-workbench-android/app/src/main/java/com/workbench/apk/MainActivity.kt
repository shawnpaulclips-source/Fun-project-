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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.workbench.apk.engine.Prefs
import com.workbench.apk.engine.Progress
import com.workbench.apk.engine.Workbench
import com.workbench.apk.ui.DoneScreen
import com.workbench.apk.ui.PickerScreen
import com.workbench.apk.ui.PipelineScreen
import com.workbench.apk.ui.WorkbenchTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.launch

private sealed interface UiState {
    data object Picker : UiState
    data class Running(val pkg: String, val label: String, val progress: Progress?) : UiState
    data class Done(val label: String, val progress: Progress) : UiState
}

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
    val scope = rememberCoroutineScope()
    val prefs = remember { Prefs(context) }
    var state by remember { mutableStateOf<UiState>(UiState.Picker) }
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

    AnimatedContent(
        targetState = state,
        transitionSpec = {
            (fadeIn() togetherWith fadeOut()).using(SizeTransform(clip = false))
        },
        label = "screen",
    ) { s ->
        when (s) {
            is UiState.Picker -> PickerScreen(
                modifier = Modifier.padding(),
                folderName = folderName,
                singleZip = singleZip,
                onPickFolder = { pickFolder.launch(null) },
                onUseDownloads = { prefs.treeUri = null; treeUri = null; folderName = null },
                onToggleSingle = { singleZip = it; prefs.singleZip = it },
                onPick = { app ->
                    state = UiState.Running(app.packageName, app.label, null)
                    val oneZip = singleZip
                    val dest = treeUri
                    scope.launch {
                        Workbench(context).run(app.packageName, oneZip, dest)
                            .flowOn(Dispatchers.IO)   // heavy pull/decompile off the UI thread
                            .catch { /* surfaced in the stage list as FAILED */ }
                            .collect { p ->
                                state = if (p.finished)
                                    UiState.Done(app.label, p)
                                else
                                    UiState.Running(app.packageName, app.label, p)
                            }
                    }
                },
            )
            is UiState.Running -> PipelineScreen(label = s.label, progress = s.progress)
            is UiState.Done -> DoneScreen(
                label = s.label,
                progress = s.progress,
                onBack = { state = UiState.Picker },
            )
        }
    }
}

/** A readable folder name out of a tree Uri (the last path segment after the ':'). */
private fun folderLabel(uri: Uri): String {
    val last = uri.lastPathSegment ?: return "chosen folder"
    return last.substringAfterLast(':').substringAfterLast('/').ifBlank { last }
}
