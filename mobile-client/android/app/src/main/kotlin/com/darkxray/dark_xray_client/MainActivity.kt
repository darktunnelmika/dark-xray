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
    private var pendingAllowLan: Boolean = true
    private var pendingDns: String = "1.1.1.1"
    private var pendingRoutingMode: String = "global"
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
                        val allowLan = call.argument<Boolean>("allowLan") ?: true
                        val dns = call.argument<String>("dns")?.trim().orEmpty().ifBlank { "1.1.1.1" }
                        val routingMode = call.argument<String>("routingMode")?.trim().orEmpty().ifBlank { "global" }
                        requestVpnAndConnect(rawUri, allowLan, dns, routingMode, result)
                    }
                    "disconnect" -> {
                        startVpnService(
                            DarkXrayVpnService.ACTION_DISCONNECT,
                            null,
                            true,
                            "1.1.1.1",
                            "global"
                        )
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

    private fun requestVpnAndConnect(
        rawUri: String,
        allowLan: Boolean,
        dns: String,
        routingMode: String,
        result: MethodChannel.Result
    ) {
        val prepareIntent = VpnService.prepare(this)
        if (prepareIntent == null) {
            startVpnService(
                DarkXrayVpnService.ACTION_CONNECT,
                rawUri,
                allowLan,
                dns,
                routingMode
            )
            result.success(null)
            return
        }
        pendingRawUri = rawUri
        pendingAllowLan = allowLan
        pendingDns = dns
        pendingRoutingMode = routingMode
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
            startVpnService(
                DarkXrayVpnService.ACTION_CONNECT,
                rawUri,
                pendingAllowLan,
                pendingDns,
                pendingRoutingMode
            )
            result?.success(null)
        } else {
            result?.error("VPN_PERMISSION", "VPN permission was not granted.", null)
        }
    }

    private fun startVpnService(
        action: String,
        rawUri: String?,
        allowLan: Boolean,
        dns: String,
        routingMode: String
    ) {
        val intent = Intent(this, DarkXrayVpnService::class.java).apply {
            this.action = action
            if (rawUri != null) putExtra(DarkXrayVpnService.EXTRA_RAW_URI, rawUri)
            putExtra(DarkXrayVpnService.EXTRA_ALLOW_LAN, allowLan)
            putExtra(DarkXrayVpnService.EXTRA_DNS, dns)
            putExtra(DarkXrayVpnService.EXTRA_ROUTING_MODE, routingMode)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            action == DarkXrayVpnService.ACTION_CONNECT) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }
}
