import 'package:flutter/services.dart';

class VpnStatus {
  const VpnStatus({
    required this.running,
    required this.error,
    required this.version,
  });

  final bool running;
  final String error;
  final String version;

  factory VpnStatus.fromMap(Map<dynamic, dynamic>? map) {
    return VpnStatus(
      running: map?['running'] == true,
      error: map?['error']?.toString() ?? '',
      version: map?['version']?.toString() ?? '',
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
  }) async {
    await _channel.invokeMethod<void>('connect', {
      'rawUri': rawUri,
      'allowLan': allowLan,
      'dns': dns,
      'routingMode': routingMode,
    });
    for (var i = 0; i < 20; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 250));
      final current = await status();
      if (current.running || current.error.isNotEmpty) return current;
    }
    return status();
  }

  Future<VpnStatus> disconnect() async {
    await _channel.invokeMethod<void>('disconnect');
    for (var i = 0; i < 12; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 180));
      final current = await status();
      if (!current.running) return current;
    }
    return status();
  }
}
