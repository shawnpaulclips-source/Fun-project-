plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.workbench.apk"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.workbench.apk"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // The decompile libraries use java.nio paths and lambdas that need desugaring on minSdk 26.
        isCoreLibraryDesugaringEnabled = true
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }

    packaging {
        resources.excludes += setOf(
            "META-INF/*.kotlin_module",
            "META-INF/LICENSE*",
            "META-INF/NOTICE*",
            "META-INF/DEPENDENCIES",
            "**/*.proto",
        )
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.02")
    implementation(composeBom)
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.animation:animation")

    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.2")

    // --- APK engine (pure-JVM, runs on Dalvik, no root, no native binaries) ---
    // Resources decode/encode without aapt2 (Maven Central):
    implementation("io.github.reandroid:ARSCLib:1.4.0")
    // dex <-> smali (Google Maven; baksmali + smali + dexlib2 fork):
    implementation("com.android.tools.smali:smali:3.0.10")
    implementation("com.android.tools.smali:smali-baksmali:3.0.10")
    implementation("com.android.tools.smali:smali-dexlib2:3.0.10")
    // Java reconstruction, read-only view (Maven Central):
    implementation("io.github.skylot:jadx-core:1.5.6")
    implementation("io.github.skylot:jadx-dex-input:1.5.6")
    // APK v1/v2/v3 signing (Maven Central):
    implementation("com.android.tools.build:apksig:2.3.0")
}
