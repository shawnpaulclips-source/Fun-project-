package com.workbench.apk.engine

import java.io.File
import java.io.InputStream
import java.util.zip.ZipFile

/**
 * The only place that touches the decompile/rebuild libraries. Each call uses the
 * library's primary, documented entry point for the version pinned in
 * app/build.gradle.kts. If a Gradle sync bumps a version and a signature moved, this
 * is the one file to adjust — the UI and the rest of the engine don't change.
 *
 * Libraries, all pure-JVM and Dalvik-safe (no root, no native aapt2):
 *   ARSCLib  (io.github.reandroid)         split merge + resource decode
 *   baksmali (com.android.tools.smali)      dex -> smali
 *   smali    (com.android.tools.smali)      smali -> dex
 *   jadx     (io.github.skylot)             dex -> Java (read-only view)
 *   apksig   (com.android.tools.build)      v1/v2/v3 signing
 */
object EngineBridge {

    // ---- merge split APKs into one -------------------------------------------------
    // ARSCLib: load the folder of base + split apks, merge the modules, write one apk.
    fun mergeSplits(apkDir: File, out: File) {
        val apks = apkDir.listFiles { f -> f.extension == "apk" }?.sortedBy { it.name } ?: emptyList()
        if (apks.isEmpty()) throw IllegalStateException("no apks to merge")
        if (apks.size == 1) { apks[0].copyTo(out, overwrite = true); return }

        // ARSCLib's ApkBundle merges a split set. Primary API:
        //   val b = com.reandroid.apk.ApkBundle(); b.loadApkDirectory(apkDir)
        //   val merged = b.mergeModules(); merged.writeApk(out)
        val apkBundleClass = Class.forName("com.reandroid.apk.ApkBundle")
        val b = apkBundleClass.getDeclaredConstructor().newInstance()
        apkBundleClass.getMethod("loadApkDirectory", File::class.java).invoke(b, apkDir)
        val merged = apkBundleClass.getMethod("mergeModules").invoke(b)
        merged.javaClass.getMethod("writeApk", File::class.java).invoke(merged, out)
    }

    // ---- dex -> smali --------------------------------------------------------------
    // baksmali disassembles each classesN.dex extracted from the apk.
    fun baksmali(apk: File, outDir: File, onDex: (done: Int, total: Int) -> Unit): Int {
        val dexEntries = ArrayList<String>()
        ZipFile(apk).use { zf ->
            val en = zf.entries()
            while (en.hasMoreElements()) {
                val n = en.nextElement().name
                if (n.endsWith(".dex") && !n.contains('/')) dexEntries.add(n)
            }
        }
        dexEntries.sort()
        val tmp = File(outDir.parentFile, "_dex").apply { mkdirs() }
        dexEntries.forEachIndexed { i, entry ->
            val dexFile = File(tmp, entry)
            ZipFile(apk).use { zf -> zf.getInputStream(zf.getEntry(entry)).use { it.copyTo(dexFile.outputStream()) } }
            val target = if (dexEntries.size == 1) outDir else File(outDir, entry.removeSuffix(".dex")).apply { mkdirs() }

            val opcodes = Class.forName("com.android.tools.smali.dexlib2.Opcodes")
                .getMethod("getDefault").invoke(null)
            val factory = Class.forName("com.android.tools.smali.dexlib2.DexFileFactory")
            val loaded = factory.getMethod(
                "loadDexFile", File::class.java,
                Class.forName("com.android.tools.smali.dexlib2.Opcodes"),
            ).invoke(null, dexFile, opcodes)

            val optionsClass = Class.forName("com.android.tools.smali.baksmali.BaksmaliOptions")
            val options = optionsClass.getDeclaredConstructor().newInstance()
            val baksmali = Class.forName("com.android.tools.smali.baksmali.Baksmali")
            val dexFileIface = Class.forName("com.android.tools.smali.dexlib2.iface.DexFile")
            baksmali.getMethod(
                "disassembleDexFile",
                dexFileIface, File::class.java, Int::class.javaPrimitiveType, optionsClass,
            ).invoke(null, loaded, target, Runtime.getRuntime().availableProcessors(), options)

            dexFile.delete()
            onDex(i + 1, dexEntries.size)
        }
        tmp.deleteRecursively()
        return dexEntries.size
    }

    // ---- dex -> Java (read-only reference) ------------------------------------------
    // jadx: standard JadxArgs -> load -> save. Returns the .java file count.
    fun jadx(apk: File, outDir: File): Int {
        val argsClass = Class.forName("jadx.api.JadxArgs")
        val args = argsClass.getDeclaredConstructor().newInstance()
        argsClass.getMethod("setInputFile", File::class.java).invoke(args, apk)
        argsClass.getMethod("setOutDir", File::class.java).invoke(args, outDir)
        // Keep going past bad classes instead of aborting the whole decompile.
        runCatching {
            argsClass.getMethod("setShowInconsistentCode", Boolean::class.javaPrimitiveType).invoke(args, true)
        }
        // Single-threaded + no debug info: much lower peak memory on a phone.
        runCatching {
            argsClass.getMethod("setThreadsCount", Int::class.javaPrimitiveType).invoke(args, 1)
        }
        runCatching {
            argsClass.getMethod("setDebugInfo", Boolean::class.javaPrimitiveType).invoke(args, false)
        }
        val decompilerClass = Class.forName("jadx.api.JadxDecompiler")
        val jadx = decompilerClass.getConstructor(argsClass).newInstance(args)
        try {
            decompilerClass.getMethod("load").invoke(jadx)
            decompilerClass.getMethod("save").invoke(jadx)
        } finally {
            runCatching { decompilerClass.getMethod("close").invoke(jadx) }
        }
        return File(outDir, "sources").walkTopDown().count { it.extension == "java" }
    }

    // ---- decode manifest + resources to editable XML (ARSCLib, no aapt2) ------------
    fun decodeResources(apk: File, outDir: File) {
        // ApkModule.loadApkFile(apk) then decode binary XML to text. Manifest first,
        // since that's what most edits touch; raw assets/res blobs are copied as-is.
        val apkModuleClass = Class.forName("com.reandroid.apk.ApkModule")
        val module = apkModuleClass.getMethod("loadApkFile", File::class.java).invoke(null, apk)

        // AndroidManifest.xml -> readable XML.
        val manifestBlock = apkModuleClass.getMethod("getAndroidManifest").invoke(module)
        val xml = manifestBlock.javaClass.getMethod("toXml").invoke(manifestBlock) // returns XMLDocument
        val manifestOut = File(outDir, "AndroidManifest.xml")
        xml.javaClass.getMethod("save", File::class.java, Boolean::class.javaPrimitiveType)
            .invoke(xml, manifestOut, true)

        // Everything else (res/, assets/, resources.arsc) extracted raw so it's all present.
        val raw = File(outDir, "raw").apply { mkdirs() }
        ZipFile(apk).use { zf ->
            val en = zf.entries()
            while (en.hasMoreElements()) {
                val e = en.nextElement()
                if (e.isDirectory || e.name.endsWith(".dex")) continue
                if (e.name == "AndroidManifest.xml") continue
                val dst = File(raw, e.name); dst.parentFile?.mkdirs()
                zf.getInputStream(e).use { it.copyTo(dst.outputStream()) }
            }
        }
    }

    // ---- harvest native libs + IL2CPP metadata from every APK ----------------------
    // Guarantees lib/*.so, global-metadata.dat and Unity Managed DLLs land in the dump,
    // even when the split that holds them wasn't folded into the merged APK.
    fun harvestNative(apks: List<File>, rawDir: File): Int {
        rawDir.mkdirs()
        var count = 0
        for (apk in apks) {
            if (!apk.isFile) continue
            runCatching {
                ZipFile(apk).use { zf ->
                    val en = zf.entries()
                    while (en.hasMoreElements()) {
                        val e = en.nextElement()
                        if (e.isDirectory) continue
                        val n = e.name
                        val wanted = n.startsWith("lib/") ||
                            n.endsWith("global-metadata.dat") ||
                            n.contains("assets/bin/Data/Managed/")
                        if (!wanted) continue
                        val dst = File(rawDir, n)
                        if (dst.isFile && dst.length() == e.size) continue  // already have it
                        dst.parentFile?.mkdirs()
                        zf.getInputStream(e).use { it.copyTo(dst.outputStream()) }
                        count++
                    }
                }
            }
        }
        return count
    }

    /** Copy the binary AndroidManifest.xml out of each APK (base first). Returns the count. */
    fun harvestManifest(apks: List<File>, rawDir: File): Int {
        rawDir.mkdirs()
        var count = 0
        apks.sortedBy { it.name }.forEachIndexed { i, apk ->
            if (!apk.isFile) return@forEachIndexed
            runCatching {
                ZipFile(apk).use { zf ->
                    val e = zf.getEntry("AndroidManifest.xml") ?: return@use
                    val dst = File(rawDir, if (i == 0) "AndroidManifest.xml" else "AndroidManifest_${apk.nameWithoutExtension}.xml")
                    zf.getInputStream(e).use { it.copyTo(dst.outputStream()) }
                    count++
                }
            }
        }
        return count
    }

    // ---- rebuild: reassemble edited smali, swap dex into the apk, sign --------------
    // Robust on-device path: assemble smali -> dex, replace classes*.dex in a copy of
    // the original apk, then apksig signs v1+v2+v3. (Resource re-encode is a PC/APKEditor
    // job; edits under resources/raw/ are swapped in verbatim here.)
    fun rebuildAndSign(originalApk: File, smaliRoot: File, resourcesRaw: File?, signer: Signer, out: File) {
        val staging = File(out.parentFile, "_rebuild").apply { deleteRecursively(); mkdirs() }

        // 1. smali -> dex for each smali tree (single tree -> classes.dex; classesN -> N).
        val smaliTrees = if (File(smaliRoot, "classes.dex").exists() || smaliRoot.listFiles()?.any { it.extension == "smali" } == true)
            listOf("classes.dex" to smaliRoot)
        else smaliRoot.listFiles { f -> f.isDirectory }?.sortedBy { it.name }?.map { dir ->
            val idx = dir.name.removePrefix("classes").ifBlank { "" }
            (if (idx.isEmpty() || idx == "1") "classes.dex" else "classes$idx.dex") to dir
        } ?: listOf("classes.dex" to smaliRoot)

        val dexFiles = LinkedHashMap<String, File>()
        for ((dexName, dir) in smaliTrees) {
            val dexOut = File(staging, dexName)
            val optionsClass = Class.forName("com.android.tools.smali.smali.SmaliOptions")
            val options = optionsClass.getDeclaredConstructor().newInstance()
            optionsClass.getField("outputDexFile").set(options, dexOut.absolutePath)
            val smali = Class.forName("com.android.tools.smali.smali.Smali")
            smali.getMethod("assemble", optionsClass, MutableList::class.java)
                .invoke(null, options, mutableListOf(dir.absolutePath))
            dexFiles[dexName] = dexOut
        }

        // 2. copy original apk, replacing the dex (and any edited raw files) in-place.
        val unsigned = File(staging, "unsigned.apk")
        val edited = resourcesRaw?.takeIf { it.isDirectory }
        java.util.zip.ZipOutputStream(unsigned.outputStream().buffered()).use { zos ->
            val replaced = HashSet<String>()
            ZipFile(originalApk).use { zf ->
                val en = zf.entries()
                while (en.hasMoreElements()) {
                    val e = en.nextElement()
                    if (e.isDirectory) continue
                    val fromEdit = edited?.let { File(it, e.name) }?.takeIf { it.isFile }
                    val fromDex = dexFiles[e.name]
                    val src: InputStream = when {
                        fromDex != null -> { replaced.add(e.name); fromDex.inputStream() }
                        fromEdit != null -> { replaced.add(e.name); fromEdit.inputStream() }
                        else -> zf.getInputStream(e)
                    }
                    zos.putNextEntry(java.util.zip.ZipEntry(e.name))
                    src.use { it.copyTo(zos) }
                    zos.closeEntry()
                }
            }
            // new dex beyond what the original had (e.g. added classes3.dex)
            for ((name, f) in dexFiles) if (name !in replaced) {
                zos.putNextEntry(java.util.zip.ZipEntry(name)); f.inputStream().use { it.copyTo(zos) }; zos.closeEntry()
            }
        }

        // 3. sign v1+v2+v3 with apksig.
        signer.sign(unsigned, out)
        staging.deleteRecursively()
    }
}
