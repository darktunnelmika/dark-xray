package com.darkxray.dark_xray_client

import android.app.Activity
import android.content.Intent
import android.net.TrafficStats
import android.net.Uri
import android.net.VpnService
import android.os.Build
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import libXray.LibXray
import org.json.JSONObject

class MainActivity : FlutterActivity() {
    private val channelName = "com.darkxray.client/vpn"
    private val vpnRequestCode = 7410

    private var pendingRawUri: String? = null
    private var pendingAllowLan: Boolean = true
    private var pendingDns: String = "1.1.1.1"
    private var pendingRoutingMode: String = "global"
    private var pendingAutoReconnect: Boolean = true
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
                        val dns = call.argument<String>("dns")
                            ?.trim()
                            .orEmpty()
                            .ifBlank { "1.1.1.1" }
                        val routingMode = call.argument<String>("routingMode")
                            ?.trim()
                            .orEmpty()
                            .ifBlank { "global" }
                        val autoReconnect = call.argument<Boolean>("autoReconnect") ?: true

                        requestVpnAndConnect(
                            rawUri,
                            allowLan,
                            dns,
                            routingMode,
                            autoReconnect,
                            result,
                        )
                    }

                    "disconnect" -> {
                        startVpnService(
                            DarkXrayVpnService.ACTION_DISCONNECT,
                            null,
                            true,
                            "1.1.1.1",
                            "global",
                            true,
                        )
                        result.success(null)
                    }

                    "status" -> {
                        result.success(buildStatus())
                    }

                    "openSystemVpnSettings" -> {
                        openIntentSafe(
                            Intent(Settings.ACTION_VPN_SETTINGS),
                            Settings.ACTION_SETTINGS,
                        )
                        result.success(null)
                    }

                    "openBatterySettings" -> {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                            openIntentSafe(
                                Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS),
                                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                            )
                        } else {
                            openIntentSafe(
                                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                                    data = Uri.parse("package:$packageName")
                                },
                                Settings.ACTION_SETTINGS,
                            )
                        }
                        result.success(null)
                    }

                    "pingProfiles" -> {
                        if (DarkXrayVpnService.running || DarkXrayVpnService.reconnecting) {
                            result.error(
                                "PING_WHILE_CONNECTED",
                                "Disconnect the VPN for full Xray route testing.",
                                null,
                            )
                            return@setMethodCallHandler
                        }

                        val rawItems = call.argument<List<String>>("rawUris")
                            ?: emptyList()
                        val timeout = (call.argument<Int>("timeoutSeconds") ?: 5)
                            .coerceIn(1, 15)

                        Thread {
                            try {
                                val delays = pingShareLinks(rawItems, timeout)
                                runOnUiThread { result.success(delays) }
                            } catch (error: Throwable) {
                                runOnUiThread {
                                    result.error(
                                        "PING_FAILED",
                                        error.message ?: "Xray ping failed.",
                                        null,
                                    )
                                }
                            }
                        }.start()
                    }

                    "convertXrayJsonToShareLinks" -> {
                        val xrayJson = call.argument<String>("xrayJson").orEmpty()
                        try {
                            val request = JSONObject()
                                .put("apiVersion", 3)
                                .put("method", "convertXrayJsonToShareLinks")
                                .put("payload", JSONObject().put("xrayJson", xrayJson))

                            val response = JSONObject(LibXray.invoke(request.toString()))
                            if (!response.optBoolean("success")) {
                                result.error(
                                    "CONVERT_FAILED",
                                    response.optString(
                                        "error",
                                        "Xray JSON conversion failed.",
                                    ),
                                    null,
                                )
                            } else {
                                val links = response.optJSONObject("data")
                                    ?.optJSONArray("links")
                                val output = mutableListOf<String>()
                                if (links != null) {
                                    for (i in 0 until links.length()) {
                                        val value = links.optString(i)
                                        if (value.isNotBlank()) output.add(value)
                                    }
                                }
                                result.success(output)
                            }
                        } catch (error: Throwable) {
                            result.error(
                                "CONVERT_FAILED",
                                error.message ?: "Xray JSON conversion failed.",
                                null,
                            )
                        }
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
        autoReconnect: Boolean,
        result: MethodChannel.Result,
    ) {
        val prepareIntent = VpnService.prepare(this)
        if (prepareIntent == null) {
            startVpnService(
                DarkXrayVpnService.ACTION_CONNECT,
                rawUri,
                allowLan,
                dns,
                routingMode,
                autoReconnect,
            )
            result.success(null)
            return
        }

        pendingRawUri = rawUri
        pendingAllowLan = allowLan
        pendingDns = dns
        pendingRoutingMode = routingMode
        pendingAutoReconnect = autoReconnect
        pendingResult = result
        startActivityForResult(prepareIntent, vpnRequestCode)
    }

    @Deprecated("Retained for Android VpnService consent compatibility.")
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
                pendingRoutingMode,
                pendingAutoReconnect,
            )
            result?.success(null)
        } else {
            result?.error(
                "VPN_PERMISSION",
                "VPN permission was not granted.",
                null,
            )
        }
    }

    private fun startVpnService(
        action: String,
        rawUri: String?,
        allowLan: Boolean,
        dns: String,
        routingMode: String,
        autoReconnect: Boolean,
    ) {
        val intent = Intent(this, DarkXrayVpnService::class.java).apply {
            this.action = action
            if (rawUri != null) putExtra(DarkXrayVpnService.EXTRA_RAW_URI, rawUri)
            putExtra(DarkXrayVpnService.EXTRA_ALLOW_LAN, allowLan)
            putExtra(DarkXrayVpnService.EXTRA_DNS, dns)
            putExtra(DarkXrayVpnService.EXTRA_ROUTING_MODE, routingMode)
            putExtra(DarkXrayVpnService.EXTRA_AUTO_RECONNECT, autoReconnect)
        }

        if (
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            action == DarkXrayVpnService.ACTION_CONNECT
        ) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }

    private fun pingShareLinks(
        rawUris: List<String>,
        timeoutSeconds: Int,
    ): List<Int> {
        if (rawUris.isEmpty()) return emptyList()

        val delays = MutableList(rawUris.size) { -1 }
        val valid = mutableListOf<Pair<Int, String>>()

        rawUris.forEachIndexed { index, rawUri ->
            try {
                val converted = invokeCore(
                    "convertShareLinksToXrayJson",
                    JSONObject().put("text", rawUri),
                )
                if (!converted.optBoolean("success")) return@forEachIndexed

                val outbounds = converted.optJSONObject("data")
                    ?.optJSONArray("outbounds")
                    ?: return@forEachIndexed

                if (outbounds.length() == 0) return@forEachIndexed

                val xrayJson = JSONObject()
                    .put("outbounds", outbounds)
                    .toString()
                valid.add(index to xrayJson)
            } catch (_: Throwable) {
            }
        }

        valid.chunked(5).forEach { batch ->
            val configs = org.json.JSONArray()
            batch.forEach { (_, xrayJson) ->
                configs.put(
                    JSONObject().put("xrayJson", xrayJson),
                )
            }

            val response = invokeCore(
                "pingBatch",
                JSONObject()
                    .put("configs", configs)
                    .put("timeout", timeoutSeconds)
                    .put("url", "https://cp.cloudflare.com/"),
            )

            if (!response.optBoolean("success")) return@forEach

            val results = response.optJSONObject("data")
                ?.optJSONArray("results")
                ?: return@forEach

            batch.forEachIndexed { localIndex, pair ->
                if (localIndex >= results.length()) return@forEachIndexed
                val item = results.optJSONObject(localIndex)
                    ?: return@forEachIndexed
                delays[pair.first] = item.optInt("delay", 10000)
            }
        }

        return delays
    }

    private fun invokeCore(method: String, payload: JSONObject): JSONObject {
        val request = JSONObject()
            .put("apiVersion", 3)
            .put("method", method)
            .put("payload", payload)
        return JSONObject(LibXray.invoke(request.toString()))
    }

    private fun buildStatus(): Map<String, Any> {
        val uid = applicationInfo.uid
        val currentRx = TrafficStats.getUidRxBytes(uid)
        val currentTx = TrafficStats.getUidTxBytes(uid)

        val rx = sessionBytes(currentRx, DarkXrayVpnService.rxBaseline)
        val tx = sessionBytes(currentTx, DarkXrayVpnService.txBaseline)

        return mapOf(
            "running" to DarkXrayVpnService.running,
            "reconnecting" to DarkXrayVpnService.reconnecting,
            "desiredConnected" to DarkXrayVpnService.desiredConnected,
            "error" to DarkXrayVpnService.lastError,
            "version" to DarkXrayVpnService.coreVersion,
            "connectedAtMs" to DarkXrayVpnService.connectedAtMs,
            "rxBytes" to rx,
            "txBytes" to tx,
        )
    }

    private fun sessionBytes(current: Long, baseline: Long): Long {
        if (current == TrafficStats.UNSUPPORTED.toLong() || baseline <= 0L) return 0L
        return (current - baseline).coerceAtLeast(0L)
    }

    private fun openIntentSafe(intent: Intent, fallbackAction: String) {
        try {
            startActivity(intent)
        } catch (_: Throwable) {
            val fallback = Intent(fallbackAction)
            if (fallbackAction == Settings.ACTION_APPLICATION_DETAILS_SETTINGS) {
                fallback.data = Uri.parse("package:$packageName")
            }
            startActivity(fallback)
        }
    }
}
