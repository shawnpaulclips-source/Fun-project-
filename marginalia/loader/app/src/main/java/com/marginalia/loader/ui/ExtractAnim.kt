package com.marginalia.loader.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke as StrokeStyle
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.marginalia.loader.Stage

private enum class RowState { PENDING, ACTIVE, DONE }

/**
 * The animated extraction pipeline: an app-to-zip stream up top, then one row per
 * stage that fills and ticks over as the extractor emits progress.
 */
@Composable
fun ExtractPipeline(
    current: Stage?,
    fractions: Map<Stage, Float>,
    done: Boolean,
    modifier: Modifier = Modifier,
) {
    Column(modifier) {
        StreamHeader(active = !done, done = done)
        Spacer(Modifier.height(16.dp))
        Stage.ordered.forEach { stage ->
            val state = when {
                done -> RowState.DONE
                current == null -> RowState.PENDING
                stage.ordinal < current.ordinal -> RowState.DONE
                stage.ordinal == current.ordinal -> RowState.ACTIVE
                else -> RowState.PENDING
            }
            val frac = if (state == RowState.DONE) 1f else fractions[stage] ?: 0f
            StageRow(stage, state, frac)
            Spacer(Modifier.height(10.dp))
        }
    }
}

@Composable
private fun StreamHeader(active: Boolean, done: Boolean) {
    val t = rememberInfiniteTransition(label = "stream")
    val dash by t.animateFloat(
        0f, 1f,
        infiniteRepeatable(tween(900, easing = LinearEasing), RepeatMode.Restart),
        label = "dash",
    )
    Row(
        Modifier.fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(Paper)
            .padding(horizontal = 16.dp, vertical = 18.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        GlyphBox(filled = true) // source apk
        Canvas(Modifier.weight(1f).height(40.dp)) {
            val y = size.height / 2
            val gap = 14f
            val dot = 4f
            val shift = if (active) dash * gap else 0f
            var x = -gap + shift
            while (x < size.width + gap) {
                val a = if (active) {
                    val d = (x / size.width).coerceIn(0f, 1f)
                    0.25f + 0.6f * kotlin.math.abs(kotlin.math.sin((d * 6.28f) + dash * 6.28f))
                } else 0.18f
                drawCircle(Ink.copy(alpha = a), dot, Offset(x, y))
                x += gap
            }
        }
        ZipGlyph(done = done)
    }
}

@Composable
private fun GlyphBox(filled: Boolean) {
    Box(
        Modifier.size(40.dp).clip(RoundedCornerShape(10.dp))
            .background(Brush.verticalGradient(listOf(SurfaceHi, Surface))),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            Modifier.size(16.dp).clip(RoundedCornerShape(4.dp))
                .background(if (filled) Brush.verticalGradient(listOf(InkBright, Ink))
                else Brush.verticalGradient(listOf(Stroke, Stroke)))
        )
    }
}

@Composable
private fun ZipGlyph(done: Boolean) {
    val col by animateColorAsState(if (done) Good else Ink, tween(260), label = "zipcol")
    Box(
        Modifier.size(40.dp).clip(RoundedCornerShape(10.dp))
            .background(Surface),
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.size(22.dp)) {
            val w = size.width; val h = size.height
            drawRoundRect(
                color = col,
                topLeft = Offset(w * 0.18f, h * 0.08f),
                size = androidx.compose.ui.geometry.Size(w * 0.64f, h * 0.84f),
                cornerRadius = androidx.compose.ui.geometry.CornerRadius(4f, 4f),
                style = StrokeStyle(width = 2.4f),
            )
            // zip teeth down the middle
            val cx = w / 2
            var yy = h * 0.14f
            var left = true
            while (yy < h * 0.9f) {
                val sx = if (left) cx - 3f else cx
                drawRect(col, Offset(sx, yy), androidx.compose.ui.geometry.Size(3f, 4f))
                yy += 7f; left = !left
            }
        }
    }
}

@Composable
private fun StageRow(stage: Stage, state: RowState, fraction: Float) {
    val f by animateFloatAsState(fraction.coerceIn(0f, 1f), tween(200), label = "sf")
    Row(verticalAlignment = Alignment.CenterVertically) {
        StageDot(state)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(
                stage.label,
                color = when (state) {
                    RowState.PENDING -> TextDim
                    RowState.ACTIVE -> TextHi
                    RowState.DONE -> TextHi
                },
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = if (state == RowState.ACTIVE) FontWeight.Bold else FontWeight.Normal,
            )
            Spacer(Modifier.height(5.dp))
            Box(
                Modifier.fillMaxWidth().height(4.dp).clip(RoundedCornerShape(50)).background(SurfaceHi)
            ) {
                Box(
                    Modifier.fillMaxWidth(if (state == RowState.PENDING) 0f else f)
                        .height(4.dp).clip(RoundedCornerShape(50))
                        .background(
                            if (state == RowState.DONE) Brush.horizontalGradient(listOf(Good, Good))
                            else Brush.horizontalGradient(listOf(Ink, InkBright))
                        )
                )
            }
        }
        Spacer(Modifier.width(10.dp))
        Text(
            "%d%%".format((f * 100).toInt()),
            color = if (state == RowState.DONE) Good else if (state == RowState.ACTIVE) Ink else TextDim,
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
        )
    }
}

@Composable
private fun StageDot(state: RowState) {
    val t = rememberInfiniteTransition(label = "dot")
    val sweep by t.animateFloat(
        0f, 360f,
        infiniteRepeatable(tween(850, easing = LinearEasing), RepeatMode.Restart),
        label = "sweep",
    )
    Box(Modifier.size(22.dp), contentAlignment = Alignment.Center) {
        when (state) {
            RowState.PENDING -> Canvas(Modifier.size(22.dp)) {
                drawCircle(Stroke, radius = size.minDimension / 2 - 1, style = StrokeStyle(1.6f))
            }
            RowState.ACTIVE -> Canvas(Modifier.size(22.dp)) {
                drawCircle(SurfaceHi, radius = size.minDimension / 2 - 1, style = StrokeStyle(2.2f))
                rotate(sweep) {
                    drawArc(
                        color = Ink, startAngle = 0f, sweepAngle = 100f, useCenter = false,
                        style = StrokeStyle(2.6f, cap = StrokeCap.Round),
                    )
                }
            }
            RowState.DONE -> {
                Canvas(Modifier.size(22.dp)) {
                    drawCircle(Good, radius = size.minDimension / 2 - 1)
                    val w = size.width
                    val path = androidx.compose.ui.graphics.Path().apply {
                        moveTo(w * 0.30f, w * 0.52f)
                        lineTo(w * 0.44f, w * 0.66f)
                        lineTo(w * 0.72f, w * 0.36f)
                    }
                    drawPath(path, Paper, style = StrokeStyle(2.4f, cap = StrokeCap.Round))
                }
            }
        }
    }
}
