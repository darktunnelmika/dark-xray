package com.darkxray.dark_xray_client

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Base64
import libXray.DialerController
import libXray.LibXray
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

class DarkXrayVpnService : VpnService() {
    companion object {
        const val ACTION_CONNECT = "com.darkxray.client.CONNECT"
        const val ACTION_DISCONNECT = "com.darkxray.client.DISCONNECT"
        const val EXTRA_RAW_URI = "rawUri"
        private const val CHANNEL_ID = "darkxray_vpn"
        private const val NOTIFICATION_ID = 7410

        @Volatile var running: Boolean = false
        @Volatile var lastError: String = ""
        @Volatile var coreVersion: String = ""
    }

    private val executor = Executors.newSingleThreadExecutor()
    private var vpnInterface: ParcelFileDescriptor? = null

    private val controller = object : DialerController {
        override fun protectFd(p0: Long): Boolean = protect(p0.toInt())
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        try {
            val response = invoke("xrayVersion", JSONObject())
            coreVersion = response.optJSONObject("data")?.optString("version").orEmpty()
        } catch (_: Throwable) {
            coreVersion = ""
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_DISCONNECT -> executor.execute { disconnectInternal(stopSelf = true) }
            ACTION_CONNECT -> {
                startForegroundCompat(buildNotification("Connecting…", false))
                val rawUri = intent.getStringExtra(EXTRA_RAW_URI).orEmpty()
                executor.execute { connectInternal(rawUri) }
            }
        }
        return Service.START_NOT_STICKY
    }

    override fun onRevoke() {
        executor.execute { disconnectInternal(stopSelf = true) }
        super.onRevoke()
    }

    override fun onDestroy() {
        disconnectInternal(stopSelf = false)
        executor.shutdownNow()
        super.onDestroy()
    }

    private fun connectInternal(rawUri: String) {
        lastError = ""
        try {
            if (rawUri.isBlank()) error("Selected profile is empty.")
            stopCoreOnly()

            val descriptor = Builder()
                .setSession("DarkXray")
                .setMtu(1500)
                .addAddress("172.19.0.1", 30)
                .addRoute("0.0.0.0", 0)
                .addDnsServer("1.1.1.1")
                .apply {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) setMetered(false)
                }
                .establish() ?: error("Android could not establish the VPN interface.")

            vpnInterface = descriptor
            LibXray.registerDialerController(controller)
            LibXray.setDNS(controller, "8.8.8.8:53")

            val config = buildXrayConfig(rawUri, descriptor.fd)
            val run = invoke(
                "runXray",
                JSONObject().put("xrayJson", config.toString())
            )
            if (!run.optBoolean("success")) {
                error(run.optString("error", "Xray failed to start."))
            }

            running = true
            lastError = ""
            val manager = getSystemService(NotificationManager::class.java)
            manager.notify(NOTIFICATION_ID, buildNotification("Connected", true))
        } catch (error: Throwable) {
            running = false
            lastError = error.message ?: error.javaClass.simpleName
            stopCoreOnly()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun disconnectInternal(stopSelf: Boolean) {
        stopCoreOnly()
        running = false
        lastError = ""
        stopForeground(STOP_FOREGROUND_REMOVE)
        if (stopSelf) stopSelf()
    }

    private fun stopCoreOnly() {
        try { invoke("stopXray", JSONObject()) } catch (_: Throwable) {}
        try { LibXray.resetDNS() } catch (_: Throwable) {}
        try { vpnInterface?.close() } catch (_: Throwable) {}
        vpnInterface = null
    }

    private fun buildXrayConfig(rawUri: String, tunFd: Int): JSONObject {
        val proxy = parseOutbound(rawUri)
        proxy.put("tag", "proxy")

        val inbounds = JSONArray().put(
            JSONObject()
                .put("tag", "tun-in")
                .put("protocol", "tun")
                .put("port", 0)
                .put(
                    "settings",
                    JSONObject()
                        .put("name", "darkxray0")
                        .put("mtu", 1500)
                )
        )

        val outbounds = JSONArray()
            .put(proxy)
            .put(JSONObject().put("tag", "direct").put("protocol", "freedom"))
            .put(JSONObject().put("tag", "block").put("protocol", "blackhole"))

        val rule = JSONObject()
            .put("type", "field")
            .put("inboundTag", JSONArray().put("tun-in"))
            .put("outboundTag", "proxy")

        return JSONObject()
            .put("log", JSONObject().put("loglevel", "warning"))
            .put("env", JSONObject().put("xray.tun.fd", tunFd.toString()))
            .put("inbounds", inbounds)
            .put("outbounds", outbounds)
            .put(
                "routing",
                JSONObject()
                    .put("domainStrategy", "AsIs")
                    .put("rules", JSONArray().put(rule))
            )
    }

    private fun parseOutbound(rawUri: String): JSONObject {
        val converted = invoke(
            "convertShareLinksToXrayJson",
            JSONObject().put("text", rawUri)
        )
        if (converted.optBoolean("success")) {
            val list = converted.optJSONObject("data")?.optJSONArray("outbounds")
            if (list != null && list.length() > 0) return JSONObject(list.getJSONObject(0).toString())
        }

        if (rawUri.startsWith("vmess://", ignoreCase = true)) {
            return parseLegacyVmess(rawUri)
        }
        error(converted.optString("error", "Unsupported or invalid proxy profile."))
    }

    private fun parseLegacyVmess(rawUri: String): JSONObject {
        val encoded = rawUri.substringAfter("vmess://").trim()
        val normalized = encoded.replace('-', '+').replace('_', '/')
        val padded = normalized + "=".repeat((4 - normalized.length % 4) % 4)
        val json = String(Base64.decode(padded, Base64.DEFAULT), Charsets.UTF_8)
        val node = JSONObject(json)
        val address = node.getString("add")
        val port = node.get("port").toString().toInt()
        val id = node.getString("id")
        val alterId = node.opt("aid")?.toString()?.toIntOrNull() ?: 0
        val security = node.optString("scy", "auto").ifBlank { "auto" }

        val user = JSONObject()
            .put("id", id)
            .put("alterId", alterId)
            .put("security", security)
        val vnext = JSONObject()
            .put("address", address)
            .put("port", port)
            .put("users", JSONArray().put(user))

        val network = node.optString("net", "tcp").ifBlank { "tcp" }.lowercase()
        val stream = JSONObject().put("network", network)
        when (network) {
            "ws" -> {
                val ws = JSONObject().put("path", node.optString("path", "/"))
                val host = node.optString("host")
                if (host.isNotBlank()) ws.put("headers", JSONObject().put("Host", host))
                stream.put("wsSettings", ws)
            }
            "grpc" -> stream.put(
                "grpcSettings",
                JSONObject().put("serviceName", node.optString("path"))
            )
            "httpupgrade" -> {
                val hu = JSONObject().put("path", node.optString("path", "/"))
                val host = node.optString("host")
                if (host.isNotBlank()) hu.put("host", host)
                stream.put("httpupgradeSettings", hu)
            }
        }

        if (node.optString("tls").equals("tls", ignoreCase = true)) {
            stream.put("security", "tls")
            val tls = JSONObject()
            val sni = node.optString("sni").ifBlank { node.optString("host") }
            if (sni.isNotBlank()) tls.put("serverName", sni)
            val fp = node.optString("fp")
            if (fp.isNotBlank()) tls.put("fingerprint", fp)
            stream.put("tlsSettings", tls)
        }

        return JSONObject()
            .put("protocol", "vmess")
            .put("settings", JSONObject().put("vnext", JSONArray().put(vnext)))
            .put("streamSettings", stream)
    }

    private fun invoke(method: String, payload: JSONObject): JSONObject {
        val request = JSONObject()
            .put("apiVersion", 3)
            .put("method", method)
            .put("payload", payload)
        return JSONObject(LibXray.invoke(request.toString()))
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "DarkXray VPN",
                NotificationManager.IMPORTANCE_LOW
            )
        )
    }

    private fun buildNotification(text: String, connected: Boolean): Notification {
        val disconnectIntent = Intent(this, DarkXrayVpnService::class.java).apply {
            action = ACTION_DISCONNECT
        }
        val disconnectPending = PendingIntent.getService(
            this,
            2,
            disconnectIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val launchIntent = packageManager.getLaunchIntentForPackage(packageName)
        val launchPending = PendingIntent.getActivity(
            this,
            1,
            launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            Notification.Builder(this)
        }

        return builder
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(if (connected) "DarkXray • Connected" else "DarkXray")
            .setContentText(text)
            .setOngoing(connected)
            .setContentIntent(launchPending)
            .addAction(
                Notification.Action.Builder(
                    R.mipmap.ic_launcher,
                    "Disconnect",
                    disconnectPending,
                ).build()
            )
            .build()
    }

    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SYSTEM_EXEMPTED,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }
}
