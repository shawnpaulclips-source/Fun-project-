package com.workbench.apk.engine

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import com.android.apksig.ApkSigner
import java.io.File
import java.security.KeyStore
import java.security.PrivateKey
import java.security.cert.X509Certificate

/**
 * Signs rebuilt APKs. The key lives in AndroidKeyStore, which hands us a self-signed
 * certificate for free, so there's no BouncyCastle and no exported private key. The
 * key is non-exportable but still usable for signing, which is all apksig needs.
 *
 * This is your own key, not the Play Store's. A rebuilt app installs only after the
 * original is uninstalled, and never updates the Play Store copy.
 */
class Signer private constructor(
    private val privateKey: PrivateKey,
    private val certs: List<X509Certificate>,
) {
    fun sign(input: File, output: File) {
        val config = ApkSigner.SignerConfig.Builder("workbench", privateKey, certs).build()
        // apksig 2.3.0: v3 is applied automatically from the signer config; only v1/v2
        // are toggled here. v2 is sufficient to install on modern Android.
        ApkSigner.Builder(listOf(config))
            .setV1SigningEnabled(true)
            .setV2SigningEnabled(true)
            .setInputApk(input)
            .setOutputApk(output)
            .build()
            .sign()
    }

    companion object {
        private const val ALIAS = "workbench-signing-key"

        fun load(): Signer {
            val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            if (!ks.containsAlias(ALIAS)) generate()
            val key = ks.getKey(ALIAS, null) as PrivateKey
            val cert = ks.getCertificate(ALIAS) as X509Certificate
            return Signer(key, listOf(cert))
        }

        private fun generate() {
            val gen = java.security.KeyPairGenerator.getInstance(
                KeyProperties.KEY_ALGORITHM_RSA, "AndroidKeyStore",
            )
            val notBefore = java.util.Date()
            val notAfter = java.util.Date(notBefore.time + 1000L * 60 * 60 * 24 * 365 * 30)
            val spec = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setKeySize(2048)
                .setDigests(KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_SHA512)
                .setSignaturePaddings(KeyProperties.SIGNATURE_PADDING_RSA_PKCS1)
                .setCertificateSubject(javax.security.auth.x500.X500Principal("CN=Workbench, O=Workbench"))
                .setCertificateSerialNumber(java.math.BigInteger.valueOf(System.currentTimeMillis()))
                .setCertificateNotBefore(notBefore)
                .setCertificateNotAfter(notAfter)
                .build()
            gen.initialize(spec)
            gen.generateKeyPair()
        }
    }
}
