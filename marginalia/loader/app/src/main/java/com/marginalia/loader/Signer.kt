package com.marginalia.loader

import android.content.Context
import com.android.apksig.ApkSigner
import org.bouncycastle.asn1.x500.X500Name
import org.bouncycastle.cert.jcajce.JcaX509CertificateConverter
import org.bouncycastle.cert.jcajce.JcaX509v3CertificateBuilder
import org.bouncycastle.jce.provider.BouncyCastleProvider
import org.bouncycastle.operator.jcajce.JcaContentSignerBuilder
import java.io.File
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Security
import java.security.cert.X509Certificate
import java.util.Date

/**
 * Real v1/v2/v3 APK signing via Google's apksig. A single self-signed key is
 * generated once and cached, so re-signed bundles install over each other.
 *
 * This key is NOT Roblox's. A re-signed Roblox will fail Roblox's own signature
 * and integrity checks for online play — this path is for analysis builds
 * (e.g. a libroblox.so you patched for a Frida gadget), not a mod you log in with.
 */
object Signer {

    private const val KS = "marginalia.p12"
    private const val ALIAS = "marginalia"
    private val PASS = "marginalia".toCharArray()

    init {
        if (Security.getProvider(BouncyCastleProvider.PROVIDER_NAME) == null) {
            Security.addProvider(BouncyCastleProvider())
        }
    }

    private data class Creds(val key: PrivateKey, val cert: X509Certificate)

    private fun creds(context: Context): Creds {
        val ksFile = File(context.filesDir, KS)
        val ks = KeyStore.getInstance("PKCS12")

        if (ksFile.exists()) {
            ksFile.inputStream().use { ks.load(it, PASS) }
            val key = ks.getKey(ALIAS, PASS) as PrivateKey
            val cert = ks.getCertificate(ALIAS) as X509Certificate
            return Creds(key, cert)
        }

        val kp = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
        val now = Date()
        val until = Date(now.time + 30L * 365 * 24 * 60 * 60 * 1000)
        val dn = X500Name("CN=Marginalia Loader, O=Marginalia, C=US")
        val serial = BigInteger.valueOf(now.time)

        val certBuilder = JcaX509v3CertificateBuilder(dn, serial, now, until, dn, kp.public)
        val signer = JcaContentSignerBuilder("SHA256withRSA")
            .setProvider(BouncyCastleProvider.PROVIDER_NAME)
            .build(kp.private)
        val cert = JcaX509CertificateConverter()
            .setProvider(BouncyCastleProvider.PROVIDER_NAME)
            .getCertificate(certBuilder.build(signer))

        ks.load(null, null)
        ks.setKeyEntry(ALIAS, kp.private, PASS, arrayOf(cert))
        ksFile.outputStream().use { ks.store(it, PASS) }

        return Creds(kp.private, cert)
    }

    fun sign(context: Context, input: File, output: File) {
        val c = creds(context)
        val config = ApkSigner.SignerConfig.Builder(
            ALIAS, c.key, listOf(c.cert)
        ).build()

        if (output.exists()) output.delete()

        ApkSigner.Builder(listOf(config))
            .setInputApk(input)
            .setOutputApk(output)
            .setV1SigningEnabled(true)
            .setV2SigningEnabled(true)
            .setV3SigningEnabled(true)
            .build()
            .sign()
    }
}
