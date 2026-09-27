import 'package:shared_preferences/shared_preferences.dart';

class DarkXraySettings {
  const DarkXraySettings({
    this.developerMode = false,
    this.allowLan = true,
    this.dns = '1.1.1.1',
    this.routingMode = 'global',
    this.pingTimeoutMs = 2500,
    this.autoReconnect = true,
    this.autoRefreshHours = 12,
  });

  final bool developerMode;
  final bool allowLan;
  final String dns;
  final String routingMode;
  final int pingTimeoutMs;
  final bool autoReconnect;
  final int autoRefreshHours;

  DarkXraySettings copyWith({
    bool? developerMode,
    bool? allowLan,
    String? dns,
    String? routingMode,
    int? pingTimeoutMs,
    bool? autoReconnect,
    int? autoRefreshHours,
  }) {
    return DarkXraySettings(
      developerMode: developerMode ?? this.developerMode,
      allowLan: allowLan ?? this.allowLan,
      dns: dns ?? this.dns,
      routingMode: routingMode ?? this.routingMode,
      pingTimeoutMs: pingTimeoutMs ?? this.pingTimeoutMs,
      autoReconnect: autoReconnect ?? this.autoReconnect,
      autoRefreshHours: autoRefreshHours ?? this.autoRefreshHours,
    );
  }
}

class SettingsStore {
  static const _developer = 'darkxray.settings.developer';
  static const _allowLan = 'darkxray.settings.allowLan';
  static const _dns = 'darkxray.settings.dns';
  static const _routing = 'darkxray.settings.routing';
  static const _pingTimeout = 'darkxray.settings.pingTimeout';
  static const _autoReconnect = 'darkxray.settings.autoReconnect';
  static const _autoRefreshHours = 'darkxray.settings.autoRefreshHours';

  Future<DarkXraySettings> load() async {
    final prefs = await SharedPreferences.getInstance();
    return DarkXraySettings(
      developerMode: prefs.getBool(_developer) ?? false,
      allowLan: prefs.getBool(_allowLan) ?? true,
      dns: prefs.getString(_dns) ?? '1.1.1.1',
      routingMode: prefs.getString(_routing) ?? 'global',
      pingTimeoutMs: prefs.getInt(_pingTimeout) ?? 2500,
      autoReconnect: prefs.getBool(_autoReconnect) ?? true,
      autoRefreshHours: prefs.getInt(_autoRefreshHours) ?? 12,
    );
  }

  Future<void> save(DarkXraySettings settings) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_developer, settings.developerMode);
    await prefs.setBool(_allowLan, settings.allowLan);
    await prefs.setString(_dns, settings.dns);
    await prefs.setString(_routing, settings.routingMode);
    await prefs.setInt(_pingTimeout, settings.pingTimeoutMs);
    await prefs.setBool(_autoReconnect, settings.autoReconnect);
    await prefs.setInt(_autoRefreshHours, settings.autoRefreshHours);
  }

  Future<void> clearAppState() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.clear();
  }
}
