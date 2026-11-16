package com.workbench.apk.ui

import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.FolderZip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.workbench.apk.engine.Progress

@Composable
fun DoneScreen(label: String, progress: Progress, onBack: () -> Unit) {
    var popped by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { popped = true }
    val scale by animateFloatAsState(
        if (popped) 1f else 0f,
        spring(dampingRatio = Spring.DampingRatioMediumBouncy, stiffness = Spring.StiffnessLow),
        label = "pop",
    )

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Spacer(Modifier.height(24.dp))
        Box(
            Modifier.size(64.dp).scale(scale).clip(RoundedCornerShape(32.dp)).background(Teal.copy(alpha = 0.16f)),
            contentAlignment = Alignment.Center,
        ) { Icon(Icons.Rounded.Check, null, tint = Teal, modifier = Modifier.size(34.dp)) }

        Spacer(Modifier.height(16.dp))
        Text("Done", style = MaterialTheme.typography.headlineMedium, color = TextHigh)
        Text(label, style = MaterialTheme.typography.bodySmall, color = TextMid)
        progress.engine.takeIf { it.isNotBlank() }?.let {
            Spacer(Modifier.height(8.dp)); EnginePill(it)
        }

        Spacer(Modifier.height(20.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Rounded.FolderZip, null, tint = Amber, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text(progress.outputDir ?: "Downloads", color = Amber, fontWeight = FontWeight.Medium)
        }
        Spacer(Modifier.height(4.dp))
        Text(
            if (progress.parts.size == 1) "one zip — whole dump in a single file"
            else "${progress.parts.size} parts under 30 MB — upload the folder to add features",
            style = MaterialTheme.typography.bodySmall, color = TextMid,
        )

        Spacer(Modifier.height(16.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.weight(1f)) {
            items(progress.parts) { part ->
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(GraphiteCard).padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(Icons.Rounded.FolderZip, null, tint = TextMid, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(10.dp))
                    Text(part, color = TextHigh, style = MaterialTheme.typography.labelMedium)
                }
            }
        }

        Box(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Amber)
                .clickable(onClick = onBack).padding(vertical = 14.dp),
            contentAlignment = Alignment.Center,
        ) { Text("Decompile another", color = Graphite, fontWeight = FontWeight.SemiBold) }
        Spacer(Modifier.height(8.dp))
    }
}
