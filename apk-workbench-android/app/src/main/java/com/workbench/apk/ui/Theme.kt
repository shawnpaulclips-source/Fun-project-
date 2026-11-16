package com.workbench.apk.ui

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// Graphite base, signal amber, muted teal — the Recoil Ledger palette.
val Graphite = Color(0xFF0E1013)
val GraphiteRaised = Color(0xFF16191F)
val GraphiteCard = Color(0xFF1C2027)
val Hairline = Color(0xFF2A2F38)
val Amber = Color(0xFFF2A93B)
val AmberDim = Color(0xFF8A6526)
val Teal = Color(0xFF4FB3A9)
val TextHigh = Color(0xFFEDEFF2)
val TextMid = Color(0xFF9AA2AD)
val TextLow = Color(0xFF5E6672)
val Danger = Color(0xFFE5564B)
val Good = Color(0xFF5FD39A)

private val WorkbenchColors = darkColorScheme(
    primary = Amber,
    onPrimary = Graphite,
    secondary = Teal,
    onSecondary = Graphite,
    background = Graphite,
    onBackground = TextHigh,
    surface = GraphiteRaised,
    onSurface = TextHigh,
    surfaceVariant = GraphiteCard,
    onSurfaceVariant = TextMid,
    outline = Hairline,
    error = Danger,
)

private val Mono = FontFamily.Monospace

private val WorkbenchType = Typography(
    headlineMedium = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 26.sp, letterSpacing = (-0.5).sp),
    titleLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 20.sp),
    titleMedium = TextStyle(fontWeight = FontWeight.Medium, fontSize = 16.sp),
    bodyMedium = TextStyle(fontWeight = FontWeight.Normal, fontSize = 14.sp, letterSpacing = 0.1.sp),
    bodySmall = TextStyle(fontWeight = FontWeight.Normal, fontSize = 12.sp, color = TextMid),
    labelSmall = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Medium, fontSize = 11.sp, letterSpacing = 0.5.sp),
    labelMedium = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Normal, fontSize = 12.sp),
)

private val WorkbenchShapes = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(8.dp),
    medium = RoundedCornerShape(12.dp),
    large = RoundedCornerShape(16.dp),
)

@Composable
fun WorkbenchTheme(content: @Composable () -> Unit) {
    // Always dark. This tool has one look.
    MaterialTheme(
        colorScheme = WorkbenchColors,
        typography = WorkbenchType,
        shapes = WorkbenchShapes,
        content = content,
    )
}
