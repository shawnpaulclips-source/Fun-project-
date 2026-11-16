package com.workbench.apk.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.slideInVertically
import androidx.compose.foundation.Image
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
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.FolderOpen
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
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
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.graphics.drawable.toBitmap
import com.workbench.apk.engine.AppScanner
import com.workbench.apk.engine.InstalledApp

@Composable
fun PickerScreen(
    modifier: Modifier = Modifier,
    folderName: String? = null,
    singleZip: Boolean = false,
    onPickFolder: () -> Unit = {},
    onUseDownloads: () -> Unit = {},
    onToggleSingle: (Boolean) -> Unit = {},
    onPick: (InstalledApp) -> Unit,
) {
    val context = LocalContext.current
    var apps by remember { mutableStateOf<List<InstalledApp>>(emptyList()) }
    var query by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(Unit) {
        apps = runCatching { AppScanner.scan(context) }.getOrDefault(emptyList())
        loading = false
    }

    val filtered = remember(apps, query) {
        if (query.isBlank()) apps
        else apps.filter { it.label.contains(query, true) || it.packageName.contains(query, true) }
    }

    Column(modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Spacer(Modifier.height(20.dp))
        Text("Workbench", style = androidx.compose.material3.MaterialTheme.typography.headlineMedium, color = TextHigh)
        Text("pick an app to decompile", style = androidx.compose.material3.MaterialTheme.typography.bodySmall, color = TextMid)
        Spacer(Modifier.height(16.dp))

        SettingsCard(
            folderName = folderName,
            singleZip = singleZip,
            onPickFolder = onPickFolder,
            onUseDownloads = onUseDownloads,
            onToggleSingle = onToggleSingle,
        )
        Spacer(Modifier.height(12.dp))

        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            modifier = Modifier.fillMaxWidth(),
            placeholder = { Text("search installed apps", color = TextLow) },
            leadingIcon = { Icon(Icons.Rounded.Search, null, tint = TextMid) },
            singleLine = true,
            shape = RoundedCornerShape(12.dp),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Amber,
                unfocusedBorderColor = Hairline,
                focusedContainerColor = GraphiteRaised,
                unfocusedContainerColor = GraphiteRaised,
                cursorColor = Amber,
                focusedTextColor = TextHigh,
                unfocusedTextColor = TextHigh,
            ),
        )
        Spacer(Modifier.height(12.dp))

        if (loading) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Spinner(size = 28.dp) }
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                itemsIndexed(filtered, key = { _, a -> a.packageName }) { index, app ->
                    var shown by remember { mutableStateOf(false) }
                    LaunchedEffect(app.packageName) { shown = true }
                    AnimatedVisibility(
                        visible = shown,
                        enter = fadeIn(tween(260, delayMillis = (index.coerceAtMost(12)) * 28)) +
                            slideInVertically(tween(260, delayMillis = (index.coerceAtMost(12)) * 28)) { it / 3 },
                    ) {
                        AppRow(app, onClick = { onPick(app) })
                    }
                }
            }
        }
    }
}

@Composable
private fun AppRow(app: InstalledApp, onClick: () -> Unit) {
    val bmp = remember(app.packageName) { runCatching { app.icon.toBitmap(96, 96).asImageBitmap() }.getOrNull() }
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(GraphiteCard)
            .clickable(onClick = onClick)
            .padding(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(44.dp).clip(RoundedCornerShape(10.dp)).background(GraphiteRaised),
            contentAlignment = Alignment.Center,
        ) {
            if (bmp != null) Image(bmp, null, Modifier.size(36.dp))
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(app.label, color = TextHigh, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(app.packageName, color = TextMid,
                style = androidx.compose.material3.MaterialTheme.typography.labelMedium,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.width(8.dp))
        Column(horizontalAlignment = Alignment.End) {
            Text("%.0f MB".format(app.sizeBytes / 1e6), color = Amber,
                style = androidx.compose.material3.MaterialTheme.typography.labelMedium)
            if (app.splitCount > 1) Text("${app.splitCount} splits", color = TextLow,
                style = androidx.compose.material3.MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
private fun SettingsCard(
    folderName: String?,
    singleZip: Boolean,
    onPickFolder: () -> Unit,
    onUseDownloads: () -> Unit,
    onToggleSingle: (Boolean) -> Unit,
) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(GraphiteCard).padding(14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Rounded.FolderOpen, null, tint = Amber, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text("Save to", color = TextMid, style = MaterialTheme.typography.labelSmall)
                Text(
                    folderName ?: "Downloads/apk-workbench",
                    color = TextHigh,
                    style = MaterialTheme.typography.labelMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.width(8.dp))
            Text(
                "Change",
                color = Teal,
                fontWeight = FontWeight.Medium,
                modifier = Modifier.clip(RoundedCornerShape(6.dp)).clickable(onClick = onPickFolder)
                    .padding(horizontal = 8.dp, vertical = 4.dp),
            )
        }
        if (folderName != null) {
            Text(
                "Using a custom folder · tap to switch back to Downloads",
                color = TextLow,
                style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.clickable(onClick = onUseDownloads).padding(top = 4.dp),
            )
        }

        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("One big zip", color = TextHigh, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Medium)
                Text(
                    if (singleZip) "whole dump in one file — for keeping / USB, too big to re-upload"
                    else "many parts under 30 MB — upload these back to add features",
                    color = TextMid,
                    style = MaterialTheme.typography.labelSmall,
                )
            }
            Spacer(Modifier.width(10.dp))
            Switch(
                checked = singleZip,
                onCheckedChange = onToggleSingle,
                colors = SwitchDefaults.colors(
                    checkedThumbColor = Graphite,
                    checkedTrackColor = Amber,
                    uncheckedThumbColor = TextMid,
                    uncheckedTrackColor = GraphiteRaised,
                    uncheckedBorderColor = Hairline,
                ),
            )
        }
    }
}

@Composable
fun Spinner(size: androidx.compose.ui.unit.Dp, color: androidx.compose.ui.graphics.Color = Amber) {
    val t = rememberInfiniteTransition(label = "spin")
    val angle by t.animateFloat(
        0f, 360f,
        infiniteRepeatable(tween(900, easing = LinearEasing), RepeatMode.Restart),
        label = "angle",
    )
    val track = Hairline
    Box(
        Modifier.size(size).drawBehind {
            val sw = 3.dp.toPx()
            drawArc(track, 0f, 360f, false, style = Stroke(sw))
            drawArc(color, angle, 90f, false, style = Stroke(sw))
        }
    )
}
