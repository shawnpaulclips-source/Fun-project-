package com.workbench.apk

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
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
import com.workbench.apk.engine.Progress
import com.workbench.apk.engine.Workbench
import com.workbench.apk.ui.DoneScreen
import com.workbench.apk.ui.PickerScreen
import com.workbench.apk.ui.PipelineScreen
import com.workbench.apk.ui.WorkbenchTheme
import kotlinx.coroutines.flow.catch
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
    var state by remember { mutableStateOf<UiState>(UiState.Picker) }

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
                onPick = { app ->
                    state = UiState.Running(app.packageName, app.label, null)
                    scope.launch {
                        Workbench(context).run(app.packageName)
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
