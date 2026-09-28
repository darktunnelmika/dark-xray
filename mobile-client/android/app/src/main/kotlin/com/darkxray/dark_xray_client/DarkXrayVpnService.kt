package com.darkxray.dark_xray_client

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.TrafficStats
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Base64
import libXray.DialerController
import libXray.LibXray
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class DarkXrayVpnService : VpnService() {
    companion object {
        const val ACTION_CONNECT = "com.darkxray.client.CONNECT"
        const val ACTION_DISCONNECT = "com.darkxray.client.DISCONNECT"

        const val EXTRA_RAW_URI = "rawUri"
        const val EXTRA_ALLOW_LAN = "allowLan"
        const val EXTRA_DNS = "dns"
        const val EXTRA_ROUTING_MODE = "routingMode"
        const val EXTRA_AUTO_RECONNECT = "autoReconnect"

        private const val CHANNEL_ID = "darkxray_vpn"
        private const val NOTIFICATION_ID = 7410
        private const val STATE_PREFS = "darkxray_vpn_state"

        @Volatile var running: Boolean = false
        @Volatile var reconnecting: Boolean = false
        @Volatile var desiredConnected: Boolean = false
        @Volatile var lastError: String = ""
        @Volatile var coreVersion: String = ""
        @Volatile var connectedAtMs: Long = 0L
        @Volatile var rxBaseline: Long = 0L
        @Volatile var txBaseline: Long = 0L
    }

    private val executor = Executors.newSingleThreadExecutor()
    private val connectGuard = AtomicBoolean(false)

    private var vpnInterface: ParcelFileDescriptor? = null
    private lateinit var connectivityManager: ConnectivityManager
    private var networkCallbackRegistered = false
    private val availableUnderlyingNetworks =
        ConcurrentHashMap.newKeySet<Network>()
    @Volatile private var underlyingNetworkLost = false

    @Volatile private var currentRawUri = ""
    @Volatile private var currentAllowLan = true
    @Volatile private var currentDns = "1.1.1.1"
    @Volatile private var currentRoutingMode = "global"
    @Volatile private var currentAutoReconnect = true

    private val controller = object : DialerController {
        override fun protectFd(p0: Long): Boolean = protect(p0.toInt())
    }

    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            val wasEmpty = availableUnderlyingNetworks.isEmpty()
            availableUnderlyingNetworks.add(network)

            if (
                wasEmpty &&
                desiredConnected &&
                currentAutoReconnect &&
                underlyingNetworkLost
            ) {
                underlyingNetworkLost = false
                executor.execute { reconnectAfterNetworkChange() }
            }
        }

        override fun onLost(network: Network) {
            availableUnderlyingNetworks.remove(network)
            if (availableUnderlyingNetworks.isNotEmpty()) return
            if (!desiredConnected || !currentAutoReconnect) return

            underlyingNetworkLost = true
            executor.execute {
                if (!desiredConnected || !currentAutoReconnect) return@execute
                reconnecting = true
                running = false
                lastError = "Network unavailable. Waiting to reconnect."
                stopXrayOnly()
                notifyState("Waiting for network…", false)
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        connectivityManager = getSystemService(ConnectivityManager::class.java)
        registerUnderlyingNetworkCallback()

        try {
            val response = invoke("xrayVersion", JSONObject())
            coreVersion = response.optJSONObject("data")?.optString("version").orEmpty()
        } catch (_: Throwable) {
            coreVersion = ""
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent == null) {
            if (restoreDesiredConnection()) {
                startForegroundCompat(buildNotification("Restoring connection…", false))
                executor.execute {
                    connectInternal(
                        currentRawUri,
                        currentAllowLan,
                        currentDns,
                        currentRoutingMode,
                        currentAutoReconnect,
                        restore = true,
                    )
                }
                return Service.START_STICKY
            }
            stopSelf()
            return Service.START_NOT_STICKY
        }

        when (intent.action) {
            ACTION_DISCONNECT -> {
                executor.execute { disconnectInternal(intentional = true) }
                return Service.START_NOT_STICKY
            }

            ACTION_CONNECT -> {
                val rawUri = intent.getStringExtra(EXTRA_RAW_URI).orEmpty()
                val allowLan = intent.getBooleanExtra(EXTRA_ALLOW_LAN, true)
                val dns = sanitizeDns(intent.getStringExtra(EXTRA_DNS).orEmpty())
                val routingMode = intent.getStringExtra(EXTRA_ROUTING_MODE)
                    ?.trim()
                    ?.lowercase()
                    .orEmpty()
                    .ifBlank { "global" }
                val autoReconnect = intent.getBooleanExtra(EXTRA_AUTO_RECONNECT, true)

                currentRawUri = rawUri
                currentAllowLan = allowLan
                currentDns = dns
                currentRoutingMode = routingMode
                currentAutoReconnect = autoReconnect
                desiredConnected = true
                persistDesiredConnection(true)

                startForegroundCompat(buildNotification("Connecting…", false))
                executor.execute {
                    connectInternal(
                        rawUri,
                        allowLan,
                        dns,
                        routingMode,
                        autoReconnect,
                        restore = false,
                    )
                }
                return Service.START_STICKY
            }
        }

        return if (desiredConnected) Service.START_STICKY else Service.START_NOT_STICKY
    }

    override fun onRevoke() {
        executor.execute { disconnectInternal(intentional = true) }
        super.onRevoke()
    }

    override fun onDestroy() {
        unregisterUnderlyingNetworkCallback()
        stopCoreAndVpn()
        running = false
        reconnecting = false
        executor.shutdownNow()
        super.onDestroy()
    }

    private fun connectInternal(
        rawUri: String,
        allowLan: Boolean,
        dns: String,
        routingMode: String,
        autoReconnect: Boolean,
        restore: Boolean,
    ) {
        if (!connectGuard.compareAndSet(false, true)) return

        reconnecting = restore
        lastError = ""

        try {
            if (rawUri.isBlank()) error("Selected profile is empty.")
            validatePlayProfile(rawUri)?.let { error(it) }

            if (running) {
                stopCoreAndVpn()
                running = false
            }

            currentRawUri = rawUri
            currentAllowLan = allowLan
            currentDns = dns
            currentRoutingMode = routingMode
            currentAutoReconnect = autoReconnect
            desiredConnected = true
            persistDesiredConnection(true)

            val descriptor = createVpnInterface(dns)
            vpnInterface = descriptor
            startCore(descriptor.fd, rawUri, allowLan, dns, routingMode)

            running = true
            reconnecting = false
            lastError = ""

            if (!restore || connectedAtMs <= 0L) {
                connectedAtMs = System.currentTimeMillis()
                rxBaseline = safeUidRxBytes()
                txBaseline = safeUidTxBytes()
            }

            notifyState("Connected", true)
        } catch (error: Throwable) {
            running = false
            reconnecting = false
            lastError = error.message ?: error.javaClass.simpleName
            desiredConnected = false
            persistDesiredConnection(false)
            stopCoreAndVpn()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        } finally {
            connectGuard.set(false)
        }
    }

    private fun reconnectAfterNetworkChange() {
        if (!desiredConnected || !currentAutoReconnect || currentRawUri.isBlank()) {
            reconnecting = false
            return
        }
        if (!connectGuard.compareAndSet(false, true)) return

        reconnecting = true
        try {
            val descriptor = vpnInterface
            if (descriptor == null) {
                connectGuard.set(false)
                connectInternal(
                    currentRawUri,
                    currentAllowLan,
                    currentDns,
                    currentRoutingMode,
                    currentAutoReconnect,
                    restore = true,
                )
                return
            }

            stopXrayOnly()
            startCore(
                descriptor.fd,
                currentRawUri,
                currentAllowLan,
                currentDns,
                currentRoutingMode,
            )
            running = true
            reconnecting = false
            lastError = ""
            notifyState("Reconnected", true)
        } catch (error: Throwable) {
            running = false
            reconnecting = true
            lastError = error.message ?: "Reconnect failed."
            notifyState("Reconnect failed • waiting for network", false)
        } finally {
            connectGuard.set(false)
        }
    }

    private fun disconnectInternal(intentional: Boolean) {
        if (intentional) {
            desiredConnected = false
            persistDesiredConnection(false)
        }

        stopCoreAndVpn()
        running = false
        reconnecting = false
        underlyingNetworkLost = false
        lastError = ""
        connectedAtMs = 0L
        rxBaseline = 0L
        txBaseline = 0L

        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun createVpnInterface(dns: String): ParcelFileDescriptor {
        return Builder()
            .setSession("DarkXray")
            .setMtu(1500)
            .addAddress("172.19.0.1", 30)
            .addAddress("fd19:db8:1::1", 126)
            .addRoute("0.0.0.0", 0)
            .addRoute("::", 0)
            .addDnsServer(dns)
            .apply {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) setMetered(false)
            }
            .establish()
            ?: error("Android could not establish the VPN interface.")
    }

    private fun startCore(
        tunFd: Int,
        rawUri: String,
        allowLan: Boolean,
        dns: String,
        routingMode: String,
    ) {
        LibXray.registerDialerController(controller)
        LibXray.setDNS(controller, "$dns:53")

        val config = buildXrayConfig(
            rawUri,
            tunFd,
            allowLan,
            routingMode,
        )
        val run = invoke(
            "runXray",
            JSONObject().put("xrayJson", config.toString()),
        )
        if (!run.optBoolean("success")) {
            error(run.optString("error", "Xray failed to start."))
        }
    }

    private fun stopXrayOnly() {
        try {
            invoke("stopXray", JSONObject())
        } catch (_: Throwable) {
        }
        try {
            LibXray.resetDNS()
        } catch (_: Throwable) {
        }
    }

    private fun stopCoreAndVpn() {
        stopXrayOnly()
        try {
            vpnInterface?.close()
        } catch (_: Throwable) {
        }
        vpnInterface = null
    }

    private fun buildXrayConfig(
        rawUri: String,
        tunFd: Int,
        allowLan: Boolean,
        routingMode: String,
    ): JSONObject {
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
                        .put("mtu", 1500),
                ),
        )

        val outbounds = JSONArray()
            .put(proxy)
            .put(JSONObject().put("tag", "direct").put("protocol", "freedom"))
            .put(JSONObject().put("tag", "block").put("protocol", "blackhole"))

        val rules = JSONArray()
        if (allowLan) {
            rules.put(
                JSONObject()
                    .put("type", "field")
                    .put(
                        "ip",
                        JSONArray()
                            .put("10.0.0.0/8")
                            .put("172.16.0.0/12")
                            .put("192.168.0.0/16")
                            .put("127.0.0.0/8")
                            .put("169.254.0.0/16")
                            .put("fc00::/7")
                            .put("fe80::/10")
                            .put("::1/128"),
                    )
                    .put("outboundTag", "direct"),
            )
        }

        rules.put(
            JSONObject()
                .put("type", "field")
                .put("inboundTag", JSONArray().put("tun-in"))
                .put(
                    "outboundTag",
                    if (routingMode == "direct") "direct" else "proxy",
                ),
        )

        return JSONObject()
            .put("log", JSONObject().put("loglevel", "warning"))
            .put("env", JSONObject().put("xray.tun.fd", tunFd.toString()))
            .put("inbounds", inbounds)
            .put("outbounds", outbounds)
            .put(
                "routing",
                JSONObject()
                    .put("domainStrategy", "AsIs")
                    .put("rules", rules),
            )
    }

    private fun validatePlayProfile(rawUri: String): String? {
        val lower = rawUri.trim().lowercase()

        if (lower.startsWith("socks://")) {
            return "Google Play builds do not allow plain SOCKS endpoints."
        }

        if (lower.startsWith("vless://")) {
            return try {
                val uri = android.net.Uri.parse(rawUri)
                val security = uri.getQueryParameter("security")
                    ?.trim()
                    ?.lowercase()
                    .orEmpty()
                if (security == "tls" || security == "reality") {
                    null
                } else {
                    "Google Play builds require VLESS with TLS or Reality."
                }
            } catch (_: Throwable) {
                "Invalid VLESS profile."
            }
        }

        if (
            lower.startsWith("trojan://") ||
            lower.startsWith("vmess://") ||
            lower.startsWith("ss://") ||
            lower.startsWith("hysteria2://") ||
            lower.startsWith("hy2://")
        ) {
            return null
        }

        return "Unsupported profile type for the Google Play build."
    }

    private fun parseOutbound(rawUri: String): JSONObject {
        val converted = invoke(
            "convertShareLinksToXrayJson",
            JSONObject().put("text", rawUri),
        )
        if (converted.optBoolean("success")) {
            val list = converted.optJSONObject("data")?.optJSONArray("outbounds")
            if (list != null && list.length() > 0) {
                return JSONObject(list.getJSONObject(0).toString())
            }
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

        val user = JSONObject()
            .put("id", node.getString("id"))
            .put("alterId", node.opt("aid")?.toString()?.toIntOrNull() ?: 0)
            .put("security", node.optString("scy", "auto").ifBlank { "auto" })

        val vnext = JSONObject()
            .put("address", node.getString("add"))
            .put("port", node.get("port").toString().toInt())
            .put("users", JSONArray().put(user))

        val network = node.optString("net", "tcp").ifBlank { "tcp" }.lowercase()
        val stream = JSONObject().put("network", network)

        when (network) {
            "ws" -> {
                val ws = JSONObject().put("path", node.optString("path", "/"))
                val host = node.optString("host")
                if (host.isNotBlank()) {
                    ws.put("headers", JSONObject().put("Host", host))
                }
                stream.put("wsSettings", ws)
            }

            "grpc" -> stream.put(
                "grpcSettings",
                JSONObject().put("serviceName", node.optString("path")),
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

    private fun sanitizeDns(value: String): String {
        val candidate = value.trim()
        if (candidate.isBlank()) return "1.1.1.1"

        val ipv4 = Regex("""^(?:\d{1,3}\.){3}\d{1,3}$""")
        if (!ipv4.matches(candidate)) return "1.1.1.1"

        val valid = candidate.split('.').all {
            val number = it.toIntOrNull() ?: return@all false
            number in 0..255
        }
        return if (valid) candidate else "1.1.1.1"
    }

    private fun persistDesiredConnection(desired: Boolean) {
        getSharedPreferences(STATE_PREFS, MODE_PRIVATE)
            .edit()
            .putBoolean("desired", desired)
            .putString("rawUri", currentRawUri)
            .putBoolean("allowLan", currentAllowLan)
            .putString("dns", currentDns)
            .putString("routingMode", currentRoutingMode)
            .putBoolean("autoReconnect", currentAutoReconnect)
            .apply()
    }

    private fun restoreDesiredConnection(): Boolean {
        val prefs = getSharedPreferences(STATE_PREFS, MODE_PRIVATE)
        val desired = prefs.getBoolean("desired", false)
        if (!desired) return false

        currentRawUri = prefs.getString("rawUri", "").orEmpty()
        currentAllowLan = prefs.getBoolean("allowLan", true)
        currentDns = sanitizeDns(prefs.getString("dns", "1.1.1.1").orEmpty())
        currentRoutingMode = prefs.getString("routingMode", "global")
            .orEmpty()
            .ifBlank { "global" }
        currentAutoReconnect = prefs.getBoolean("autoReconnect", true)

        desiredConnected = currentRawUri.isNotBlank()
        return desiredConnected
    }

    private fun registerUnderlyingNetworkCallback() {
        if (networkCallbackRegistered) return
        try {
            val request = NetworkRequest.Builder()
                .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                .build()
            connectivityManager.registerNetworkCallback(request, networkCallback)
            networkCallbackRegistered = true
        } catch (_: Throwable) {
        }
    }

    private fun unregisterUnderlyingNetworkCallback() {
        if (!networkCallbackRegistered) return
        try {
            connectivityManager.unregisterNetworkCallback(networkCallback)
        } catch (_: Throwable) {
        }
        networkCallbackRegistered = false
        availableUnderlyingNetworks.clear()
    }

    private fun safeUidRxBytes(): Long {
        val value = TrafficStats.getUidRxBytes(applicationInfo.uid)
        return if (value == TrafficStats.UNSUPPORTED.toLong()) 0L else value
    }

    private fun safeUidTxBytes(): Long {
        val value = TrafficStats.getUidTxBytes(applicationInfo.uid)
        return if (value == TrafficStats.UNSUPPORTED.toLong()) 0L else value
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "DarkXray VPN",
                NotificationManager.IMPORTANCE_LOW,
            ),
        )
    }

    private fun notifyState(text: String, connected: Boolean) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIFICATION_ID, buildNotification(text, connected))
    }

    private fun buildNotification(text: String, connected: Boolean): Notification {
        val disconnectIntent = Intent(this, DarkXrayVpnService::class.java).apply {
            action = ACTION_DISCONNECT
        }
        val disconnectPending = PendingIntent.getService(
            this,
            2,
            disconnectIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
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
            .setContentTitle(
                when {
                    reconnecting -> "DarkXray • Reconnecting"
                    connected -> "DarkXray • Connected"
                    else -> "DarkXray"
                },
            )
            .setContentText(text)
            .setOngoing(desiredConnected)
            .setContentIntent(launchPending)
            .addAction(
                Notification.Action.Builder(
                    R.mipmap.ic_launcher,
                    "Disconnect",
                    disconnectPending,
                ).build(),
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
