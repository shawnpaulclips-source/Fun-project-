package com.marginalia.loader.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

val Ink        = Color(0xFFE8943B)   // margin-note amber
val InkBright  = Color(0xFFF2B560)
val Paper      = Color(0xFF121316)
val Surface    = Color(0xFF191B20)
val SurfaceHi  = Color(0xFF22252C)
val Stroke     = Color(0xFF2E323C)
val TextHi     = Color(0xFFECEEF3)
val TextDim    = Color(0xFF858C98)
val Good       = Color(0xFF60D48C)
val Bad        = Color(0xFFEE5C5C)

private val scheme = darkColorScheme(
    primary = Ink,
    onPrimary = Color(0xFF1A1206),
    secondary = InkBright,
    background = Paper,
    onBackground = TextHi,
    surface = Surface,
    onSurface = TextHi,
    surfaceVariant = SurfaceHi,
    onSurfaceVariant = TextDim,
    outline = Stroke,
    error = Bad,
)

private val type = Typography(
    headlineSmall = TextStyle(fontWeight = FontWeight.Black, fontSize = 22.sp, letterSpacing = 0.5.sp),
    titleMedium = TextStyle(fontWeight = FontWeight.Bold, fontSize = 15.sp),
    bodyMedium = TextStyle(fontWeight = FontWeight.Normal, fontSize = 14.sp),
    labelSmall = TextStyle(fontWeight = FontWeight.Medium, fontSize = 11.sp, letterSpacing = 0.4.sp),
)

@Composable
fun MarginaliaTheme(content: @Composable () -> Unit) {
    isSystemInDarkTheme() // app is dark regardless; keep the hook for future light theme
    MaterialTheme(colorScheme = scheme, typography = type, content = content)
}
