package com.marginalia.loader

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.marginalia.loader.ui.*

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MarginaliaTheme {
                Surface(color = Paper, modifier = Modifier.fillMaxSize()) {
                    App()
                }
            }
        }
    }
}

@Composable
private fun App() {
    var tab by remember { mutableStateOf(0) }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        Header()
        TabBar(tab) { tab = it }
        Box(Modifier.weight(1f).navigationBarsPadding()) {
            when (tab) {
                0 -> ExtractScreen()
                else -> RepackageScreen()
            }
        }
    }
}

@Composable
private fun Header() {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(22.dp).clip(RoundedCornerShape(6.dp))
                .background(Brush.verticalGradient(listOf(InkBright, Ink)))
        )
        Spacer(Modifier.width(10.dp))
        Text("MARGINALIA", color = TextHi, style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.width(8.dp))
        Text("loader", color = TextDim, style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.align(Alignment.Bottom).padding(bottom = 3.dp))
    }
}

@Composable
private fun TabBar(tab: Int, onTab: (Int) -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp).padding(bottom = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        TabButton("EXTRACT", tab == 0, Modifier.weight(1f)) { onTab(0) }
        TabButton("REPACKAGE", tab == 1, Modifier.weight(1f)) { onTab(1) }
    }
}

@Composable
private fun TabButton(label: String, on: Boolean, modifier: Modifier, onClick: () -> Unit) {
    val bg by animateColorAsState(if (on) Surface else Paper, tween(180), label = "tab")
    val fg by animateColorAsState(if (on) Ink else TextDim, tween(180), label = "tabfg")
    Surface(
        color = bg,
        shape = RoundedCornerShape(10.dp),
        border = if (on) androidx.compose.foundation.BorderStroke(1.dp, Stroke) else null,
        onClick = onClick,
        modifier = modifier.height(38.dp),
    ) {
        Box(contentAlignment = Alignment.Center) {
            Text(label, color = fg, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Black)
        }
    }
}
