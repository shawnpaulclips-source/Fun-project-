package com.marginalia.loader.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Folder
import androidx.compose.material.icons.rounded.Share
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.marginalia.loader.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.io.File

@Composable
fun RepackageScreen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var bundle by remember { mutableStateOf<Uri?>(null) }
    var bundleName by remember { mutableStateOf("") }
    var lib by remember { mutableStateOf<Uri?>(null) }
    var libName by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var progress by remember { mutableStateOf(0f) }
    var note by remember { mutableStateOf("") }
    var output by remember { mutableStateOf<List<File>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }

    val pickBundle = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) { bundle = uri; bundleName = uri.lastPathSegment ?: "bundle.zip"; output = emptyList() }
    }
    val pickLib = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) { lib = uri; libName = uri.lastPathSegment ?: "libroblox.so"; output = emptyList() }
    }

    Column(
        Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            "Swap a modified libroblox.so into a bundle's arm64 split and re-sign the set so it installs. " +
                "The signing key is Marginalia's, not Roblox's — a re-signed client won't pass Roblox's own " +
                "online integrity checks. Use this for analysis builds (e.g. a gadget-patched lib), not a mod you log in with.",
            color = TextDim, style = MaterialTheme.typography.labelSmall,
        )

        PickCard("BUNDLE ZIP", bundleName.ifBlank { "pick the bundle from the Extract tab" },
            bundle != null) { pickBundle.launch(arrayOf("application/zip", "application/octet-stream", "*/*")) }

        PickCard("REPLACEMENT libroblox.so", libName.ifBlank { "pick your modified .so" },
            lib != null) { pickLib.launch(arrayOf("application/octet-stream", "*/*")) }

        AnimatedVisibility(busy) {
            Column {
                ProgressBar(progress)
                Spacer(Modifier.height(6.dp))
                Text(note, color = Ink, style = MaterialTheme.typography.labelSmall)
            }
        }

        error?.let { Text("failed: $it", color = Bad, style = MaterialTheme.typography.labelSmall) }

        if (output.isNotEmpty()) {
            Surface(color = Paper, shape = RoundedCornerShape(12.dp), border = BorderStroke(1.dp, Good)) {
                Column(Modifier.padding(14.dp)) {
                    Text("SIGNED — ${output.size} apk(s)", color = Good,
                        style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Black)
                    Spacer(Modifier.height(6.dp))
                    output.forEach {
                        Text("${it.name}  ·  ${human(it.length())}", color = TextHi,
                            style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace)
                    }
                    Spacer(Modifier.height(10.dp))
                    Text("install:  adb install-multiple " + output.joinToString(" ") { it.name },
                        color = TextDim, style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace)
                    Spacer(Modifier.height(12.dp))
                    OutlinedButton(
                        onClick = { Share.send(context, output, "Send signed apks") },
                        border = BorderStroke(1.dp, Ink),
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = Ink),
                        shape = RoundedCornerShape(10.dp),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Icon(Icons.Rounded.Share, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp))
                        Text("EXPORT SIGNED SET", fontWeight = FontWeight.Bold)
                    }
                }
            }
        }

        Button(
            onClick = {
                val b = bundle; val l = lib
                if (busy || b == null || l == null) return@Button
                busy = true; progress = 0f; note = "starting"; output = emptyList(); error = null
                scope.launch(Dispatchers.Default) {
                    Repackager.rebuild(context, b, l) { step ->
                        when (step) {
                            is RepackStep.Progress -> { progress = step.fraction; note = step.note }
                            is RepackStep.Done -> { busy = false; progress = 1f; output = step.signedApks }
                            is RepackStep.Failed -> { busy = false; error = step.reason }
                        }
                    }
                }
            },
            enabled = !busy && bundle != null && lib != null,
            colors = ButtonDefaults.buttonColors(
                containerColor = Ink, contentColor = Paper,
                disabledContainerColor = SurfaceHi, disabledContentColor = TextDim,
            ),
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier.fillMaxWidth().height(50.dp),
        ) {
            Text(if (busy) "REBUILDING…" else "REBUILD & SIGN", fontWeight = FontWeight.Black)
        }
        Spacer(Modifier.height(20.dp))
    }
}

@Composable
private fun PickCard(title: String, value: String, filled: Boolean, onClick: () -> Unit) {
    Surface(
        color = Surface,
        shape = RoundedCornerShape(12.dp),
        border = if (filled) BorderStroke(1.5.dp, Ink) else CardBorder,
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Rounded.Folder, null, tint = if (filled) Ink else TextDim)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(title, color = if (filled) Ink else TextDim,
                    style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Black)
                Text(value, color = TextHi, style = MaterialTheme.typography.bodyMedium, fontFamily = FontFamily.Monospace)
            }
        }
    }
}
