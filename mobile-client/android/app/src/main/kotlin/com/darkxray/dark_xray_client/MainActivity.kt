package com.darkxray.dark_xray_client

import android.app.Activity
import android.content.Intent
import android.net.VpnService
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val channelName = "com.darkxray.client/vpn"
    private val vpnRequestCode = 7410
    private var pendingRawUri: String? = null
    private var pendingResult: MethodChannel.Result? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "connect" -> {
                        val rawUri = call.argument<String>("rawUri")?.trim().orEmpty()
                        if (rawUri.isEmpty()) {
                            result.error("NO_PROFILE", "No proxy profile selected.", null)
                            return@setMethodCallHandler
                        }
                        requestVpnAndConnect(rawUri, result)
                    }
                    "disconnect" -> {
                        startVpnService(DarkXrayVpnService.ACTION_DISCONNECT, null)
                        result.success(null)
                    }
                    "status" -> {
                        result.success(
                            mapOf(
                                "running" to DarkXrayVpnService.running,
                                "error" to DarkXrayVpnService.lastError,
                                "version" to DarkXrayVpnService.coreVersion
                            )
                        )
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun requestVpnAndConnect(rawUri: String, result: MethodChannel.Result) {
        val prepareIntent = VpnService.prepare(this)
        if (prepareIntent == null) {
            startVpnService(DarkXrayVpnService.ACTION_CONNECT, rawUri)
            result.success(null)
            return
        }
        pendingRawUri = rawUri
        pendingResult = result
        startActivityForResult(prepareIntent, vpnRequestCode)
    }

    @Deprecated("Deprecated in Android API; retained for VpnService consent compatibility.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != vpnRequestCode) return
        val result = pendingResult
        val rawUri = pendingRawUri
        pendingResult = null
        pendingRawUri = null
        if (resultCode == Activity.RESULT_OK && rawUri != null) {
            startVpnService(DarkXrayVpnService.ACTION_CONNECT, rawUri)
            result?.success(null)
        } else {
            result?.error("VPN_PERMISSION", "VPN permission was not granted.", null)
        }
    }

    private fun startVpnService(action: String, rawUri: String?) {
        val intent = Intent(this, DarkXrayVpnService::class.java).apply {
            this.action = action
            if (rawUri != null) putExtra(DarkXrayVpnService.EXTRA_RAW_URI, rawUri)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            action == DarkXrayVpnService.ACTION_CONNECT) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }
}
