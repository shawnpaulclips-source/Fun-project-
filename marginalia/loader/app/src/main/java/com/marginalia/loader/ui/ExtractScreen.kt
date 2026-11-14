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
import androidx.compose.runtime.mutableStateMapOf
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
    var currentStage by remember { mutableStateOf<Stage?>(null) }
    val fractions = remember { mutableStateMapOf<Stage, Float>() }
    var note by remember { mutableStateOf("") }
    var parts by remember { mutableStateOf<List<File>>(emptyList()) }
    var resultLib by remember { mutableStateOf<LibInfo?>(null) }
    var done by remember { mutableStateOf(false) }
    var libOnly by remember { mutableStateOf(true) }
    var chunkMb by remember { mutableStateOf(28) }
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
                AppRow(app, app.pkg == selected?.pkg) { selected = app; parts = emptyList(); done = false; error = null }
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

                    OptionsRow(libOnly, { libOnly = it }, chunkMb, { chunkMb = it }, enabled = !busy)

                    AnimatedVisibility(busy || done) {
                        Column(Modifier.padding(top = 14.dp)) {
                            ExtractPipeline(
                                current = currentStage,
                                fractions = fractions,
                                done = done,
                            )
                            if (busy) {
                                Spacer(Modifier.height(10.dp))
                                Text(note, style = MaterialTheme.typography.labelSmall, color = Ink,
                                    fontFamily = FontFamily.Monospace)
                            }
                        }
                    }

                    if (done && parts.isNotEmpty()) {
                        Spacer(Modifier.height(12.dp))
                        PartsCard(parts, resultLib) { Share.send(context, parts, "Send parts") }
                    }

                    error?.let {
                        Spacer(Modifier.height(10.dp))
                        Text("failed: $it", color = Bad, style = MaterialTheme.typography.labelSmall)
                    }

                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = {
                            if (busy) return@Button
                            busy = true; note = "starting"; parts = emptyList(); done = false; error = null
                            currentStage = null; fractions.clear()
                            val cap = chunkMb.toLong() * 1024 * 1024
                            scope.launch(Dispatchers.Default) {
                                Extractor.extract(context, app, cap, libOnly) { step ->
                                    when (step) {
                                        is Step.Progress -> {
                                            currentStage = step.stage
                                            fractions[step.stage] = step.stageFraction
                                            note = step.note
                                        }
                                        is Step.Done -> {
                                            busy = false; done = true
                                            parts = step.parts; resultLib = step.lib
                                        }
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
                        Text(if (busy) "EXTRACTING…" else if (libOnly) "EXTRACT libroblox.so → PARTS" else "EXTRACT BUNDLE → PARTS", fontWeight = FontWeight.Black)
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
private fun OptionsRow(
    libOnly: Boolean, onLibOnly: (Boolean) -> Unit,
    chunkMb: Int, onChunk: (Int) -> Unit, enabled: Boolean,
) {
    Column(Modifier.padding(top = 12.dp)) {
        Row(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(Paper)
                .clickable(enabled = enabled) { onLibOnly(!libOnly) }
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("libroblox.so only", color = TextHi, style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold)
                Text(if (libOnly) "smallest — just what the dump needs" else "full split bundle (large)",
                    color = TextDim, style = MaterialTheme.typography.labelSmall)
            }
            MiniToggle(libOnly) { onLibOnly(!libOnly) }
        }
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("part size", color = TextDim, style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.padding(end = 10.dp))
            listOf(20, 25, 28).forEach { mb ->
                val on = chunkMb == mb
                Surface(
                    color = if (on) Ink else Paper,
                    shape = RoundedCornerShape(8.dp),
                    border = if (on) null else CardBorder,
                    onClick = { if (enabled) onChunk(mb) },
                    modifier = Modifier.padding(end = 6.dp),
                ) {
                    Text("${mb}MB", color = if (on) Paper else TextDim,
                        style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 7.dp))
                }
            }
        }
    }
}

@Composable
private fun MiniToggle(on: Boolean, onTap: () -> Unit) {
    val bg = if (on) Ink else SurfaceHi
    Box(
        Modifier.size(width = 40.dp, height = 23.dp).clip(RoundedCornerShape(50)).background(bg)
            .clickable(onClick = onTap),
        contentAlignment = if (on) Alignment.CenterEnd else Alignment.CenterStart,
    ) {
        Box(Modifier.padding(horizontal = 3.dp).size(17.dp).clip(RoundedCornerShape(50))
            .background(if (on) Paper else TextDim))
    }
}

@Composable
private fun PartsCard(parts: List<File>, lib: LibInfo?, onShare: () -> Unit) {
    val pieces = parts.filter { !it.name.endsWith(".parts.json") }
    val manifest = parts.firstOrNull { it.name.endsWith(".parts.json") }
    val stem = pieces.firstOrNull()?.name?.substringBeforeLast('.') ?: "payload"
    val totalBytes = pieces.sumOf { it.length() }
    Surface(color = Paper, shape = RoundedCornerShape(12.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, Good)) {
        Column(Modifier.padding(14.dp)) {
            Text("READY — ${pieces.size} PART(S), ${human(totalBytes)}", color = Good,
                style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Black)
            Spacer(Modifier.height(6.dp))
            pieces.forEach {
                Text("${it.name}  ·  ${human(it.length())}", color = TextHi,
                    style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            if (lib != null) {
                Spacer(Modifier.height(8.dp))
                Text("libroblox.so sha256", color = InkBright, style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace)
                Text(lib.sha256, color = TextDim, style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.height(8.dp))
            Text("reassemble:  cat $stem.* > $stem", color = TextDim,
                style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(12.dp))
            OutlinedButton(
                onClick = onShare,
                border = androidx.compose.foundation.BorderStroke(1.dp, Ink),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = Ink),
                shape = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Rounded.Share, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp))
                Text("SHARE ALL PARTS", fontWeight = FontWeight.Bold)
            }
        }
    }
}
