package com.workbench.apk.engine

import kotlinx.coroutines.flow.MutableStateFlow

/**
 * Shared decompile state, so the work lives in the foreground service and the UI just
 * observes it. Leaving the app (opening TikTok) or coming back never loses progress —
 * the service keeps running and writing here until it finishes or the app is closed.
 */
sealed interface RunState {
    data object Idle : RunState
    data class Running(val pkg: String, val label: String, val progress: Progress?) : RunState
    data class Done(val label: String, val progress: Progress) : RunState
    data class Failed(val label: String, val message: String) : RunState
}

object PipelineBus {
    val state = MutableStateFlow<RunState>(RunState.Idle)
    val isActive: Boolean get() = state.value is RunState.Running
}
