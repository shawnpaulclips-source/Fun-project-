package com.marginalia.loader.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

@Composable
fun ProgressBar(fraction: Float, modifier: Modifier = Modifier, h: Dp = 6.dp) {
    val f by animateFloatAsState(fraction.coerceIn(0f, 1f), tween(220), label = "pf")
    Box(
        modifier
            .fillMaxWidth()
            .height(h)
            .clip(RoundedCornerShape(50))
            .background(SurfaceHi)
    ) {
        Box(
            Modifier
                .fillMaxWidth(f)
                .height(h)
                .clip(RoundedCornerShape(50))
                .background(Brush.horizontalGradient(listOf(Ink, InkBright)))
        )
    }
}

fun human(bytes: Long): String {
    if (bytes < 1024) return "$bytes B"
    val kb = bytes / 1024.0
    if (kb < 1024) return "%.0f KB".format(kb)
    val mb = kb / 1024.0
    if (mb < 1024) return "%.1f MB".format(mb)
    return "%.2f GB".format(mb / 1024.0)
}

val CardBorder = BorderStroke(1.dp, Stroke)
