import 'package:shared_preferences/shared_preferences.dart';

import '../models/proxy_profile.dart';

class ProfileStore {
  static const _profilesKey = 'darkxray.profiles.v1';
  static const _sourceKey = 'darkxray.subscription.url';
  static const _syncKey = 'darkxray.subscription.syncedAt';
  static const _selectedProfileKey = 'darkxray.profile.selectedId';

  Future<List<ProxyProfile>> loadProfiles() async {
    final prefs = await SharedPreferences.getInstance();
    final items = prefs.getStringList(_profilesKey) ?? const <String>[];
    return items.map(ProxyProfile.decode).toList(growable: false);
  }

  Future<void> saveProfiles(
    List<ProxyProfile> profiles, {
    String? sourceUrl,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList(
      _profilesKey,
      profiles.map((profile) => profile.encode()).toList(),
    );
    if (sourceUrl != null && sourceUrl.trim().isNotEmpty) {
      await prefs.setString(_sourceKey, sourceUrl.trim());
    }
    await prefs.setString(_syncKey, DateTime.now().toIso8601String());
  }

  Future<void> saveLocalProfiles(List<ProxyProfile> profiles) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList(
      _profilesKey,
      profiles.map((profile) => profile.encode()).toList(),
    );
  }

  Future<void> clearProfiles() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_profilesKey);
    await prefs.remove(_sourceKey);
    await prefs.remove(_syncKey);
    await prefs.remove(_selectedProfileKey);
  }

  Future<void> saveSelectedProfileId(String? id) async {
    final prefs = await SharedPreferences.getInstance();
    if (id == null || id.trim().isEmpty) {
      await prefs.remove(_selectedProfileKey);
    } else {
      await prefs.setString(_selectedProfileKey, id.trim());
    }
  }

  Future<String?> loadSelectedProfileId() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_selectedProfileKey);
  }

  Future<String?> loadSourceUrl() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_sourceKey);
  }

  Future<DateTime?> loadLastSync() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_syncKey);
    return raw == null ? null : DateTime.tryParse(raw);
  }
}
