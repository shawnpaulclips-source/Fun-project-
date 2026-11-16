package com.workbench.apk.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.scaleIn
import androidx.compose.ui.geometry.Offset
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.workbench.apk.engine.Progress
import com.workbench.apk.engine.Stage
import com.workbench.apk.engine.StageState
import com.workbench.apk.engine.StageStatus

@Composable
fun PipelineScreen(label: String, progress: Progress?) {
    val stages = progress?.stages ?: Stage.values().map { StageState(it) }
    val overall = remember(stages) {
        val done = stages.count { it.status == StageStatus.DONE }
        val active = stages.firstOrNull { it.status == StageStatus.RUNNING }?.progress ?: 0f
        ((done + active) / stages.size).coerceIn(0f, 1f)
    }
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Spacer(Modifier.height(20.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(label, style = MaterialTheme.typography.titleLarge, color = TextHigh)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    PulsingLabel("decompiling")
                    progress?.engine?.takeIf { it.isNotBlank() }?.let {
                        Spacer(Modifier.width(8.dp)); EnginePill(it)
                    }
                }
            }
            ProgressDial(overall)
        }
        Spacer(Modifier.height(20.dp))

        Column {
            stages.forEachIndexed { i, s ->
                StageRow(s, isLast = i == stages.lastIndex)
            }
        }

        Spacer(Modifier.height(16.dp))
        Text("LOG", style = MaterialTheme.typography.labelSmall, color = TextLow)
        Spacer(Modifier.height(6.dp))
        LogConsole(progress?.log ?: emptyList(), Modifier.weight(1f).fillMaxWidth())
        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun StageRow(s: StageState, isLast: Boolean) {
    val nodeColor = when (s.status) {
        StageStatus.DONE -> Teal
        StageStatus.RUNNING -> Amber
        StageStatus.FAILED -> Danger
        StageStatus.SKIPPED -> TextLow
        StageStatus.PENDING -> Hairline
    }
    Row(Modifier.fillMaxWidth().height(if (isLast) 44.dp else 52.dp)) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.width(32.dp)) {
            Box(contentAlignment = Alignment.Center, modifier = Modifier.size(26.dp)) {
                when (s.status) {
                    StageStatus.RUNNING -> Spinner(size = 26.dp, color = Amber)
                    StageStatus.DONE -> StatusGlyph(Icons.Rounded.Check, Teal)
                    StageStatus.FAILED -> StatusGlyph(Icons.Rounded.Close, Danger)
                    else -> Box(
                        Modifier.size(10.dp).clip(RoundedCornerShape(5.dp)).background(nodeColor)
                    )
                }
            }
            if (!isLast) {
                val connectorFilled = s.status == StageStatus.DONE
                val fill by animateFloatAsState(if (connectorFilled) 1f else 0f, tween(400), label = "conn")
                Box(
                    Modifier.width(2.dp).weight(1f).drawBehind {
                        drawRect(Hairline)
                        drawRect(Teal, size = size.copy(height = size.height * fill))
                    }
                )
            }
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f).padding(top = 2.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    s.stage.label,
                    color = if (s.status == StageStatus.PENDING) TextLow else TextHigh,
                    fontWeight = if (s.status == StageStatus.RUNNING) FontWeight.SemiBold else FontWeight.Normal,
                )
                if (s.detail.isNotBlank()) {
                    Spacer(Modifier.width(8.dp))
                    Text(s.detail, color = TextMid, style = MaterialTheme.typography.labelMedium)
                }
            }
            if (s.status == StageStatus.RUNNING) {
                Spacer(Modifier.height(6.dp))
                if (s.progress > 0f) {
                    val p by animateFloatAsState(s.progress, tween(200), label = "p")
                    Box(
                        Modifier.fillMaxWidth().height(3.dp).clip(RoundedCornerShape(2.dp))
                            .drawBehind {
                                drawRect(Hairline)
                                drawRect(Amber, size = size.copy(width = size.width * p))
                            }
                    )
                } else {
                    IndeterminateBar()
                }
            }
        }
    }
}

@Composable
private fun StatusGlyph(icon: androidx.compose.ui.graphics.vector.ImageVector, tint: Color) {
    AnimatedVisibility(visible = true, enter = scaleIn(tween(220)) + fadeIn(tween(220))) {
        Box(
            Modifier.size(26.dp).clip(RoundedCornerShape(13.dp)).background(tint.copy(alpha = 0.15f)),
            contentAlignment = Alignment.Center,
        ) { Icon(icon, null, tint = tint, modifier = Modifier.size(16.dp)) }
    }
}

@Composable
private fun ProgressDial(fraction: Float) {
    val f by animateFloatAsState(fraction, tween(300), label = "dial")
    Box(contentAlignment = Alignment.Center, modifier = Modifier.size(52.dp)) {
        Box(
            Modifier.size(52.dp).drawBehind {
                val sw = 4.dp.toPx()
                drawArc(Hairline, 0f, 360f, false, style = androidx.compose.ui.graphics.drawscope.Stroke(sw))
                drawArc(Amber, -90f, 360f * f, false, style = androidx.compose.ui.graphics.drawscope.Stroke(sw))
            }
        )
        Text("${(f * 100).toInt()}%", color = TextHigh, style = MaterialTheme.typography.labelMedium)
    }
}

@Composable
private fun PulsingLabel(text: String) {
    val t = rememberInfiniteTransition(label = "pulse")
    val a by t.animateFloat(0.45f, 1f, infiniteRepeatable(tween(900, easing = LinearEasing), RepeatMode.Reverse), label = "a")
    val dots by t.animateFloat(0f, 3.99f, infiniteRepeatable(tween(1200, easing = LinearEasing), RepeatMode.Restart), label = "dots")
    Text(
        text + ".".repeat(dots.toInt()),
        color = Amber.copy(alpha = a),
        style = MaterialTheme.typography.bodySmall,
    )
}

@Composable
private fun IndeterminateBar() {
    val t = rememberInfiniteTransition(label = "indet")
    val x by t.animateFloat(-0.4f, 1f, infiniteRepeatable(tween(1100, easing = LinearEasing), RepeatMode.Restart), label = "x")
    Box(
        Modifier.fillMaxWidth().height(3.dp).clip(RoundedCornerShape(2.dp)).drawBehind {
            drawRect(Hairline)
            val w = size.width * 0.4f
            val start = (x * size.width).coerceIn(-w, size.width)
            drawRect(Amber, topLeft = Offset(start, 0f), size = size.copy(width = w))
        }
    )
}

@Composable
fun EnginePill(engine: String) {
    Box(
        Modifier.clip(RoundedCornerShape(6.dp)).background(Teal.copy(alpha = 0.14f))
            .padding(horizontal = 8.dp, vertical = 2.dp),
    ) { Text(engine, color = Teal, style = MaterialTheme.typography.labelSmall) }
}

@Composable
private fun LogConsole(lines: List<String>, modifier: Modifier = Modifier) {
    val scroll = rememberScrollState()
    LaunchedEffect(lines.size) { scroll.animateScrollTo(scroll.maxValue) }
    Box(modifier.clip(RoundedCornerShape(10.dp)).background(Color(0xFF0A0C0F)).padding(12.dp)) {
        Column(Modifier.fillMaxWidth().verticalScroll(scroll)) {
            if (lines.isEmpty()) {
                Text("…", color = TextLow, style = MaterialTheme.typography.labelMedium)
            }
            lines.forEach { line ->
                val tint = when {
                    line.startsWith("error") -> Danger
                    line.startsWith("note") -> Amber
                    line.startsWith("saved") -> Teal
                    else -> TextMid
                }
                Text(line, color = tint, style = MaterialTheme.typography.labelMedium)
            }
        }
    }
}
