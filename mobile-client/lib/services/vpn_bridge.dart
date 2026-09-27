import 'package:flutter/services.dart';

class VpnStatus {
  const VpnStatus({
    required this.running,
    required this.error,
    required this.version,
    required this.rxBytes,
    required this.txBytes,
    required this.connectedAtMs,
    required this.reconnecting,
    required this.desiredConnected,
  });

  final bool running;
  final String error;
  final String version;
  final int rxBytes;
  final int txBytes;
  final int connectedAtMs;
  final bool reconnecting;
  final bool desiredConnected;

  Duration get connectedDuration {
    if (connectedAtMs <= 0) return Duration.zero;
    final delta = DateTime.now().millisecondsSinceEpoch - connectedAtMs;
    return Duration(milliseconds: delta < 0 ? 0 : delta);
  }

  factory VpnStatus.fromMap(Map<dynamic, dynamic>? map) {
    int asInt(dynamic value) {
      if (value is int) return value;
      if (value is num) return value.toInt();
      return int.tryParse(value?.toString() ?? '') ?? 0;
    }

    return VpnStatus(
      running: map?['running'] == true,
      error: map?['error']?.toString() ?? '',
      version: map?['version']?.toString() ?? '',
      rxBytes: asInt(map?['rxBytes']),
      txBytes: asInt(map?['txBytes']),
      connectedAtMs: asInt(map?['connectedAtMs']),
      reconnecting: map?['reconnecting'] == true,
      desiredConnected: map?['desiredConnected'] == true,
    );
  }
}

class VpnBridge {
  static const _channel = MethodChannel('com.darkxray.client/vpn');

  Future<VpnStatus> status() async {
    final map = await _channel.invokeMethod<Map<dynamic, dynamic>>('status');
    return VpnStatus.fromMap(map);
  }

  Future<VpnStatus> connect(
    String rawUri, {
    bool allowLan = true,
    String dns = '1.1.1.1',
    String routingMode = 'global',
    bool autoReconnect = true,
  }) async {
    await _channel.invokeMethod<void>('connect', {
      'rawUri': rawUri,
      'allowLan': allowLan,
      'dns': dns,
      'routingMode': routingMode,
      'autoReconnect': autoReconnect,
    });

    for (var i = 0; i < 28; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 250));
      final current = await status();
      if (current.running ||
          current.error.isNotEmpty ||
          current.reconnecting) {
        return current;
      }
    }
    return status();
  }

  Future<List<String>> convertXrayJsonToShareLinks(String xrayJson) async {
    final links = await _channel.invokeMethod<List<dynamic>>(
      'convertXrayJsonToShareLinks',
      {'xrayJson': xrayJson},
    );
    return (links ?? const <dynamic>[])
        .map((item) => item.toString())
        .where((item) => item.trim().isNotEmpty)
        .toList(growable: false);
  }

  Future<void> openSystemVpnSettings() =>
      _channel.invokeMethod<void>('openSystemVpnSettings');

  Future<void> openBatterySettings() =>
      _channel.invokeMethod<void>('openBatterySettings');

  Future<VpnStatus> disconnect() async {
    await _channel.invokeMethod<void>('disconnect');
    for (var i = 0; i < 16; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 180));
      final current = await status();
      if (!current.running && !current.reconnecting) return current;
    }
    return status();
  }
}
