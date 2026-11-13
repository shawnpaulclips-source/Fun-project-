package com.marginalia.loader.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.Share
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.graphics.drawable.toBitmap
import com.marginalia.loader.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

@Composable
fun ExtractScreen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var apps by remember { mutableStateOf<List<AppEntry>>(emptyList()) }
    var query by remember { mutableStateOf("") }
    var selected by remember { mutableStateOf<AppEntry?>(null) }
    var busy by remember { mutableStateOf(false) }
    var progress by remember { mutableStateOf(0f) }
    var note by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<Pair<File, LibInfo?>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        apps = withContext(Dispatchers.Default) { AppScanner.scan(context) }
    }

    val filtered = remember(apps, query) {
        if (query.isBlank()) apps
        else apps.filter { it.label.contains(query, true) || it.pkg.contains(query, true) }
    }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {

        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            placeholder = { Text("search installed apps", color = TextDim) },
            leadingIcon = { Icon(Icons.Rounded.Search, null, tint = TextDim) },
            singleLine = true,
            keyboardOptions = KeyboardOptions.Default,
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Ink,
                unfocusedBorderColor = Stroke,
                focusedTextColor = TextHi,
                unfocusedTextColor = TextHi,
                cursorColor = Ink,
            ),
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
        )

        Spacer(Modifier.height(10.dp))

        LazyColumn(
            Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(filtered, key = { it.pkg }) { app ->
                AppRow(app, app.pkg == selected?.pkg) { selected = app; result = null; error = null }
            }
            item { Spacer(Modifier.height(140.dp)) }
        }
    }

    // bottom action sheet
    selected?.let { app ->
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
            Surface(
                color = Surface,
                shape = RoundedCornerShape(topStart = 18.dp, topEnd = 18.dp),
                border = CardBorder,
                shadowElevation = 16.dp,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(16.dp)) {
                    Text(app.label, style = MaterialTheme.typography.titleMedium, color = TextHi)
                    Text(
                        "${app.pkg}  ·  v${app.versionName} (${app.versionCode})  ·  ${app.splitCount} splits  ·  ${human(app.totalSize)}",
                        style = MaterialTheme.typography.labelSmall, color = TextDim,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )

                    AnimatedVisibility(busy) {
                        Column(Modifier.padding(top = 12.dp)) {
                            ProgressBar(progress)
                            Spacer(Modifier.height(6.dp))
                            Text(note, style = MaterialTheme.typography.labelSmall, color = Ink)
                        }
                    }

                    result?.let { (zip, lib) ->
                        Spacer(Modifier.height(12.dp))
                        ResultCard(zip, lib) { Share.send(context, listOf(zip), "Send bundle") }
                    }

                    error?.let {
                        Spacer(Modifier.height(10.dp))
                        Text("failed: $it", color = Bad, style = MaterialTheme.typography.labelSmall)
                    }

                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = {
                            if (busy) return@Button
                            busy = true; progress = 0f; note = "starting"; result = null; error = null
                            scope.launch(Dispatchers.Default) {
                                Extractor.extract(context, app) { step ->
                                    when (step) {
                                        is Step.Progress -> { progress = step.fraction; note = step.note }
                                        is Step.Done -> { busy = false; progress = 1f; result = step.zip to step.lib }
                                        is Step.Failed -> { busy = false; error = step.reason }
                                    }
                                }
                            }
                        },
                        enabled = !busy,
                        colors = ButtonDefaults.buttonColors(containerColor = Ink, contentColor = Paper),
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth().height(50.dp),
                    ) {
                        Text(if (busy) "EXTRACTING…" else "EXTRACT BUNDLE TO ZIP", fontWeight = FontWeight.Black)
                    }
                }
            }
        }
    }
}

@Composable
private fun AppRow(app: AppEntry, sel: Boolean, onClick: () -> Unit) {
    val bmp = remember(app.pkg) { runCatching { app.icon.toBitmap(96, 96).asImageBitmap() }.getOrNull() }
    Surface(
        color = if (sel) SurfaceHi else Surface,
        shape = RoundedCornerShape(12.dp),
        border = if (sel) androidx.compose.foundation.BorderStroke(1.5.dp, Ink) else CardBorder,
        modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(onClick = onClick),
    ) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            if (bmp != null) {
                Image(bmp, null, Modifier.size(40.dp).clip(RoundedCornerShape(9.dp)))
            } else {
                Box(Modifier.size(40.dp).clip(RoundedCornerShape(9.dp)).background(SurfaceHi))
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(app.label, color = TextHi, style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(
                    "${app.pkg}  ·  ${app.splitCount} splits  ·  ${human(app.totalSize)}",
                    color = TextDim, style = MaterialTheme.typography.labelSmall,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            if (sel) Icon(Icons.Rounded.CheckCircle, null, tint = Ink)
        }
    }
}

@Composable
private fun ResultCard(zip: File, lib: LibInfo?, onShare: () -> Unit) {
    Surface(color = Paper, shape = RoundedCornerShape(12.dp), border = androidx.compose.foundation.BorderStroke(1.dp, Good)) {
        Column(Modifier.padding(14.dp)) {
            Text("BUNDLE READY", color = Good, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Black)
            Spacer(Modifier.height(6.dp))
            Text(zip.name, color = TextHi, style = MaterialTheme.typography.bodyMedium, fontFamily = FontFamily.Monospace)
            Text(human(zip.length()), color = TextDim, style = MaterialTheme.typography.labelSmall)
            if (lib != null) {
                Spacer(Modifier.height(8.dp))
                Text(lib.entry, color = InkBright, style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace)
                Text("sha256 ${lib.sha256}", color = TextDim, style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace, maxLines = 2, overflow = TextOverflow.Ellipsis)
            } else {
                Spacer(Modifier.height(6.dp))
                Text("no libroblox.so in this bundle", color = Bad, style = MaterialTheme.typography.labelSmall)
            }
            Spacer(Modifier.height(12.dp))
            OutlinedButton(
                onClick = onShare,
                border = androidx.compose.foundation.BorderStroke(1.dp, Ink),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = Ink),
                shape = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Rounded.Share, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp)); Text("SHARE / SEND", fontWeight = FontWeight.Bold)
            }
        }
    }
}
