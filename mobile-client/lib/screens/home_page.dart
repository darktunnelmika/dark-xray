import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/proxy_profile.dart';
import '../services/import_resolver.dart';
import '../services/profile_store.dart';
import '../services/play_compliance_store.dart';
import '../services/play_security.dart';
import '../services/settings_store.dart';
import '../services/subscription_service.dart';
import '../services/vpn_bridge.dart';
import '../ui/cyber.dart';
import '../widgets/vpn_disclosure_dialog.dart';
import 'add_configuration_page.dart';
import 'qr_scanner_page.dart';
import 'settings_page.dart';
import 'subscriptions_page.dart';

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage>
    with SingleTickerProviderStateMixin, WidgetsBindingObserver {
  final _profileStore = ProfileStore();
  final _playCompliance = PlayComplianceStore();
  final _settingsStore = SettingsStore();
  final _vpn = VpnBridge();
  final _resolver = const ImportResolver();
  final _subscription = const SubscriptionService();

  late final AnimationController _pulse;
  Timer? _statusTimer;
  Timer? _refreshTimer;
  List<ProxyProfile> _profiles = const [];
  DarkXraySettings _settings = const DarkXraySettings();
  int _selected = 0;
  bool _connected = false;
  bool _vpnBusy = false;
  bool _pingingAll = false;
  bool _loading = true;
  String _sourceMeta = 'No subscription loaded';
  String _coreVersion = '';
  String _coreError = '';
  VpnStatus? _vpnStatus;

  Color get _accent => _connected ? CyberPalette.cyan : CyberPalette.red;

  ProxyProfile? get _selectedProfile {
    if (_profiles.isEmpty || _selected >= _profiles.length) return null;
    return _profiles[_selected];
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _pulse = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1700),
    )..repeat(reverse: true);
    _reloadAll();
    _statusTimer = Timer.periodic(
      const Duration(seconds: 2),
      (_) => _pollVpnState(),
    );
    _refreshTimer = Timer.periodic(
      const Duration(minutes: 15),
      (_) => _maybeAutoRefreshSubscription(),
    );
  }

  Future<void> _pollVpnState() async {
    try {
      final status = await _vpn.status();
      if (!mounted) return;
      setState(() {
        _vpnStatus = status;
        _connected = status.running;
        _coreVersion = status.version;
        _coreError = status.error;
      });
    } catch (_) {}
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) return;
    unawaited(_pollVpnState());
    unawaited(_maybeAutoRefreshSubscription());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _statusTimer?.cancel();
    _refreshTimer?.cancel();
    _pulse.dispose();
    super.dispose();
  }

  Future<void> _reloadAll() async {
    try {
      final profiles = await _profileStore.loadProfiles();
      final settings = await _settingsStore.load();
      final lastSync = await _profileStore.loadLastSync();
      final source = await _profileStore.loadSourceUrl();
      final selectedId = await _profileStore.loadSelectedProfileId();

      VpnStatus? vpnStatus;
      try {
        vpnStatus = await _vpn.status();
      } catch (_) {}

      if (!mounted) return;
      setState(() {
        _profiles = profiles;
        _settings = settings;
        final restoredIndex = selectedId == null
            ? -1
            : profiles.indexWhere((profile) => profile.id == selectedId);
        _selected = profiles.isEmpty
            ? 0
            : restoredIndex >= 0
                ? restoredIndex
                : _selected.clamp(0, profiles.length - 1);
        _vpnStatus = vpnStatus;
        _connected = vpnStatus?.running ?? false;
        _coreVersion = vpnStatus?.version ?? '';
        _coreError = vpnStatus?.error ?? '';
        _sourceMeta = _formatSourceMeta(source, lastSync, profiles.length);
        _loading = false;
      });
      unawaited(_maybeAutoRefreshSubscription());
    } catch (error) {
      if (!mounted) return;
      setState(() => _loading = false);
      _show('Could not load DarkXray state: ' + error.toString());
    }
  }

  String _formatSourceMeta(String? source, DateTime? sync, int count) {
    if (count == 0) return 'No subscription loaded';
    if (source == null || source.isEmpty) {
      return count.toString() + ' local profiles';
    }
    if (sync == null) {
      return count.toString() + ' profiles • subscription saved';
    }

    final age = DateTime.now().difference(sync);
    String stamp;
    if (age.inMinutes < 1) {
      stamp = 'just now';
    } else if (age.inMinutes < 60) {
      stamp = age.inMinutes.toString() + 'm ago';
    } else if (age.inHours < 24) {
      stamp = age.inHours.toString() + 'h ago';
    } else {
      stamp = age.inDays.toString() + 'd ago';
    }
    return count.toString() + ' profiles • synced ' + stamp;
  }

  void _show(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  Future<void> _toggleVpn() async {
    if (_vpnBusy) return;
    final profile = _selectedProfile;

    if (!_connected && (profile == null || profile.rawUri.trim().isEmpty)) {
      _show('Add a real configuration first.');
      return;
    }

    if (!_connected) {
      final securityError =
          PlaySecurity.validateEncryptedEndpoint(profile!.rawUri);
      if (securityError != null) {
        _show(securityError);
        return;
      }

      final accepted = await _playCompliance.hasAcceptedVpnDisclosure();
      if (!accepted) {
        if (!mounted) return;
        final consented = await VpnDisclosureDialog.show(context);
        if (!consented) return;
        await _playCompliance.acceptVpnDisclosure();
      }
    }

    setState(() => _vpnBusy = true);
    try {
      final status = _connected
          ? await _vpn.disconnect()
          : await _vpn.connect(
              profile!.rawUri,
              allowLan: _settings.allowLan,
              dns: _settings.dns,
              routingMode: _settings.routingMode,
              autoReconnect: _settings.autoReconnect,
            );

      if (!mounted) return;
      setState(() {
        _vpnStatus = status;
        _connected = status.running;
        _coreError = status.error;
        _coreVersion = status.version;
      });
      if (status.error.isNotEmpty) _show(status.error);
    } on PlatformException catch (error) {
      _show(error.message ?? error.code);
    } catch (error) {
      _show('VPN error: ' + error.toString());
    } finally {
      if (mounted) setState(() => _vpnBusy = false);
    }
  }

  Future<void> _openAddConfig() async {
    final imported = await Navigator.of(context).push<List<ProxyProfile>>(
      MaterialPageRoute(builder: (_) => const AddConfigurationPage()),
    );
    if (imported == null || imported.isEmpty) return;
    await _mergeProfiles(imported);
  }

  Future<void> _openSubscriptions() async {
    final imported = await Navigator.of(context).push<List<ProxyProfile>>(
      MaterialPageRoute(builder: (_) => const SubscriptionsPage()),
    );
    if (imported != null && imported.isNotEmpty && mounted) {
      setState(() {
        _profiles = imported;
        _selected = 0;
      });
      await _profileStore.saveSelectedProfileId(imported.first.id);
    }
    await _reloadAll();
  }

  Future<void> _openSettings() async {
    await Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const SettingsPage()),
    );
    await _reloadAll();
  }

  Future<void> _mergeProfiles(List<ProxyProfile> incoming) async {
    final map = <String, ProxyProfile>{};
    for (final profile in _profiles) {
      if (profile.rawUri.isNotEmpty) map[profile.rawUri] = profile;
    }
    for (final profile in incoming) {
      if (profile.rawUri.trim().isNotEmpty) map[profile.rawUri] = profile;
    }
    final merged = map.values.toList(growable: false);
    await _profileStore.saveLocalProfiles(merged);
    if (!mounted) return;
    setState(() {
      _profiles = merged;
      _selected = merged.isEmpty ? 0 : merged.length - 1;
      _sourceMeta = merged.length.toString() + ' local profiles';
    });
    if (merged.isNotEmpty) {
      await _profileStore.saveSelectedProfileId(merged[_selected].id);
    }
    _show('Imported ' + incoming.length.toString() + ' profile(s).');
  }

  Future<void> _importRaw(String raw) async {
    try {
      final profiles = await _resolver.resolve(raw);
      final uri = Uri.tryParse(raw.trim());
      final isSource = uri != null &&
          (uri.scheme == 'http' || uri.scheme == 'https') &&
          uri.hasAuthority;

      if (isSource) {
        await _profileStore.saveProfiles(profiles, sourceUrl: raw.trim());
        if (!mounted) return;
        setState(() {
          _profiles = profiles;
          _selected = 0;
        });
        if (profiles.isNotEmpty) {
          await _profileStore.saveSelectedProfileId(profiles.first.id);
        }
        await _reloadAll();
        _show('Subscription imported: ' + profiles.length.toString() + ' profiles.');
      } else {
        await _mergeProfiles(profiles);
      }
    } on SubscriptionException catch (error) {
      _show(error.message);
    } catch (error) {
      _show('Import failed: ' + error.toString());
    }
  }

  Future<void> _importClipboard() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final raw = data?.text?.trim();
    if (raw == null || raw.isEmpty) {
      _show('Clipboard is empty.');
      return;
    }
    await _importRaw(raw);
  }

  Future<void> _scanQr() async {
    final raw = await Navigator.of(context).push<String>(
      MaterialPageRoute(builder: (_) => const QrScannerPage()),
    );
    if (raw == null || raw.trim().isEmpty) return;
    await _importRaw(raw);
  }

  Future<void> _selectProfile(int index) async {
    if (index < 0 || index >= _profiles.length) return;
    setState(() => _selected = index);
    await _profileStore.saveSelectedProfileId(_profiles[index].id);
  }

  Future<void> _maybeAutoRefreshSubscription() async {
    if (_settings.autoRefreshHours <= 0) return;

    final source = await _profileStore.loadSourceUrl();
    if (source == null || source.trim().isEmpty) return;

    final lastSync = await _profileStore.loadLastSync();
    if (lastSync != null) {
      final age = DateTime.now().difference(lastSync);
      if (age < Duration(hours: _settings.autoRefreshHours)) return;
    }

    await _refreshSubscription(silent: true);
  }

  Future<void> _refreshSubscription({bool silent = false}) async {
    final source = await _profileStore.loadSourceUrl();
    if (source == null || source.trim().isEmpty) {
      if (!silent) await _openSubscriptions();
      return;
    }

    final selectedId = _selectedProfile?.id;
    try {
      final profiles = await _subscription.fetchAndParse(source);
      await _profileStore.saveProfiles(profiles, sourceUrl: source);
      if (!mounted) return;

      var nextIndex = 0;
      if (selectedId != null) {
        final found = profiles.indexWhere((profile) => profile.id == selectedId);
        if (found >= 0) nextIndex = found;
      }

      setState(() {
        _profiles = profiles;
        _selected = profiles.isEmpty ? 0 : nextIndex;
      });

      if (profiles.isNotEmpty) {
        await _profileStore.saveSelectedProfileId(profiles[_selected].id);
      }
      await _reloadAll();

      if (!silent) {
        _show('Subscription refreshed: ' +
            profiles.length.toString() +
            ' profiles.');
      }
    } on SubscriptionException catch (error) {
      if (!silent) _show(error.message);
    } catch (error) {
      if (!silent) _show('Refresh failed: ' + error.toString());
    }
  }

  Future<ProxyProfile> _measureProfile(ProxyProfile profile) async {
    final host = profile.host;
    final port = profile.port;
    if (host == null || host.isEmpty || port == null || port <= 0) {
      return profile.copyWith(ping: 'n/a');
    }

    final watch = Stopwatch()..start();
    try {
      final socket = await Socket.connect(
        host,
        port,
        timeout: Duration(milliseconds: _settings.pingTimeoutMs),
      );
      watch.stop();
      socket.destroy();
      return profile.copyWith(
        ping: watch.elapsedMilliseconds.toString() + ' ms',
      );
    } catch (_) {
      return profile.copyWith(ping: 'timeout');
    }
  }

  int _pingValue(ProxyProfile profile) {
    final match = RegExp(r'^(\d+)').firstMatch(profile.ping);
    if (match == null) return 1 << 30;
    return int.tryParse(match.group(1) ?? '') ?? (1 << 30);
  }

  Future<List<ProxyProfile>> _tcpPingFallback(
    List<ProxyProfile> input,
  ) async {
    final output = <ProxyProfile>[];
    const batchSize = 8;

    for (var start = 0; start < input.length; start += batchSize) {
      final rawEnd = start + batchSize;
      final end = rawEnd > input.length ? input.length : rawEnd;
      final batch = input.sublist(start, end);
      output.addAll(await Future.wait(batch.map(_measureProfile)));
    }
    return output;
  }

  Future<void> _pingAllAndSort() async {
    if (_pingingAll || _profiles.isEmpty) return;

    final selectedId = _selectedProfile?.id;
    setState(() => _pingingAll = true);

    try {
      List<ProxyProfile> results;

      final canUseCorePing =
          !_connected && !(_vpnStatus?.reconnecting ?? false);
      if (canUseCorePing) {
        try {
          final timeoutSeconds =
              ((_settings.pingTimeoutMs + 999) ~/ 1000).clamp(1, 15);
          final delays = await _vpn.pingProfiles(
            _profiles.map((profile) => profile.rawUri).toList(growable: false),
            timeoutSeconds: timeoutSeconds,
          );

          results = <ProxyProfile>[];
          for (var i = 0; i < _profiles.length; i++) {
            final profile = _profiles[i];
            final delay = i < delays.length ? delays[i] : -1;

            if (delay >= 0 && delay < 10000) {
              results.add(profile.copyWith(ping: '$delay ms'));
            } else if (delay == 10000 || delay == 11000) {
              results.add(profile.copyWith(ping: 'timeout'));
            } else {
              results.add(await _measureProfile(profile));
            }
          }
        } on PlatformException {
          results = await _tcpPingFallback(_profiles);
        } catch (_) {
          results = await _tcpPingFallback(_profiles);
        }
      } else {
        results = await _tcpPingFallback(_profiles);
      }

      results.sort((a, b) {
        final pingCompare = _pingValue(a).compareTo(_pingValue(b));
        if (pingCompare != 0) return pingCompare;
        return a.name.toLowerCase().compareTo(b.name.toLowerCase());
      });

      await _profileStore.saveLocalProfiles(results);

      var nextIndex = 0;
      if (selectedId != null) {
        final found = results.indexWhere((profile) => profile.id == selectedId);
        if (found >= 0) nextIndex = found;
      }

      if (!mounted) return;
      setState(() {
        _profiles = results;
        _selected = results.isEmpty ? 0 : nextIndex;
      });

      if (results.isNotEmpty) {
        await _profileStore.saveSelectedProfileId(results[_selected].id);
      }
    } finally {
      if (mounted) setState(() => _pingingAll = false);
    }
  }

  Future<void> _copySelected() async {
    final profile = _selectedProfile;
    if (profile == null) return;
    await Clipboard.setData(ClipboardData(text: profile.rawUri));
    _show('Selected configuration copied.');
  }

  Future<void> _removeSelected() async {
    if (_connected) {
      _show('Disconnect before removing the active profile.');
      return;
    }
    if (_selectedProfile == null) return;

    final updated = List<ProxyProfile>.from(_profiles)..removeAt(_selected);
    await _profileStore.saveLocalProfiles(updated);
    if (!mounted) return;

    setState(() {
      _profiles = updated;
      _selected = updated.isEmpty ? 0 : _selected.clamp(0, updated.length - 1);
      _sourceMeta = updated.isEmpty
          ? 'No subscription loaded'
          : updated.length.toString() + ' local profiles';
    });

    await _profileStore.saveSelectedProfileId(
      updated.isEmpty ? null : updated[_selected].id,
    );
  }

  Future<void> _showDetails() async {
    final profile = _selectedProfile;
    if (profile == null) return;

    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: CyberPalette.panel,
        title: Text(
          profile.name,
          style: const TextStyle(color: CyberPalette.text),
        ),
        content: SelectableText(
          'Protocol: ' + profile.scheme.toUpperCase() + '\n'
          'Host: ' + (profile.host ?? 'unknown') + '\n'
          'Port: ' + (profile.port?.toString() ?? 'unknown') + '\n'
          'Latency: ' + profile.ping + '\n\n' + profile.detail,
          style: const TextStyle(
            color: CyberPalette.muted,
            height: 1.5,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('CLOSE'),
          ),
        ],
      ),
    );
  }

  Future<void> _showMore() async {
    if (_profiles.isEmpty) {
      await _openAddConfig();
      return;
    }

    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: CyberPalette.panel,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.sync_rounded, color: CyberPalette.red),
              title: const Text('Refresh subscription'),
              onTap: () {
                Navigator.pop(context);
                _refreshSubscription();
              },
            ),
            ListTile(
              leading: const Icon(Icons.copy_rounded, color: CyberPalette.red),
              title: const Text('Copy selected configuration'),
              onTap: () {
                Navigator.pop(context);
                _copySelected();
              },
            ),
            ListTile(
              leading: const Icon(Icons.info_outline_rounded, color: CyberPalette.red),
              title: const Text('Profile details'),
              onTap: () {
                Navigator.pop(context);
                _showDetails();
              },
            ),
            ListTile(
              leading: const Icon(Icons.delete_outline_rounded, color: CyberPalette.red),
              title: const Text('Remove selected profile'),
              onTap: () {
                Navigator.pop(context);
                _removeSelected();
              },
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Stack(
        children: [
          const Positioned.fill(child: CyberBackground()),
          SafeArea(
            child: Column(
              children: [
                _TopBar(
                  onSettings: _openSettings,
                  onAdd: _openAddConfig,
                ),
                Expanded(
                  child: _loading
                      ? const Center(
                          child: CircularProgressIndicator(color: CyberPalette.red),
                        )
                      : ListView(
                          padding: const EdgeInsets.fromLTRB(18, 8, 18, 24),
                          children: [
                            _PowerSection(
                              connected: _connected,
                              reconnecting:
                                  _vpnStatus?.reconnecting ?? false,
                              busy: _vpnBusy,
                              accent: _accent,
                              animation: _pulse,
                              error: _coreError,
                              onTap: _toggleVpn,
                            ),
                            if ((_vpnStatus?.desiredConnected ?? false) ||
                                _connected ||
                                (_vpnStatus?.reconnecting ?? false)) ...[
                              const SizedBox(height: 14),
                              _SessionStats(
                                status: _vpnStatus,
                                accent: _accent,
                              ),
                            ],
                            if (_coreError.isNotEmpty && !_connected) ...[
                              const SizedBox(height: 12),
                              _CoreErrorBanner(
                                error: _coreError,
                              ),
                            ],
                            const SizedBox(height: 20),
                            CyberFrame(
                              accent: _accent,
                              child: Column(
                                children: [
                                  _SubscriptionHeader(
                                    accent: _accent,
                                    subtitle: _sourceMeta,
                                    onTap: _openSubscriptions,
                                  ),
                                  const SizedBox(height: 12),
                                  _StatsRow(
                                    profileCount: _profiles.length,
                                    selected: _selectedProfile,
                                    accent: _accent,
                                  ),
                                  const SizedBox(height: 12),
                                  Row(
                                    children: [
                                      Expanded(
                                        child: CyberActionButton(
                                          label: _pingingAll
                                              ? 'PINGING…'
                                              : 'PING ALL',
                                          icon: Icons.speed_rounded,
                                          accent: _accent,
                                          onTap: _pingingAll
                                              ? () {}
                                              : _pingAllAndSort,
                                        ),
                                      ),
                                      const SizedBox(width: 10),
                                      IconButton(
                                        tooltip: 'More',
                                        onPressed: _showMore,
                                        icon: Icon(
                                          Icons.more_horiz_rounded,
                                          color: _accent,
                                        ),
                                        style: IconButton.styleFrom(
                                          backgroundColor: CyberPalette.panelSoft,
                                          side: BorderSide(
                                            color: _accent.withValues(alpha: 0.6),
                                          ),
                                        ),
                                      ),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                            const SizedBox(height: 14),
                            if (_profiles.isEmpty)
                              _EmptyProfiles(
                                onAdd: _openAddConfig,
                                accent: _accent,
                              )
                            else
                              CyberFrame(
                                accent: _accent,
                                child: Column(
                                  children: [
                                    for (var i = 0; i < _profiles.length; i++)
                                      _ProfileTile(
                                        profile: _profiles[i],
                                        selected: _selected == i,
                                        accent: _accent,
                                        onTap: () => _selectProfile(i),
                                      ),
                                  ],
                                ),
                              ),
                            const SizedBox(height: 14),
                            Row(
                              children: [
                                Expanded(
                                  child: CyberActionButton(
                                    label: 'CLIPBOARD',
                                    icon: Icons.content_paste_rounded,
                                    accent: _accent,
                                    onTap: _importClipboard,
                                  ),
                                ),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: CyberActionButton(
                                    label: 'QR SCAN',
                                    icon: Icons.qr_code_scanner_rounded,
                                    accent: _accent,
                                    onTap: _scanQr,
                                  ),
                                ),
                              ],
                            ),
                            if (_settings.developerMode) ...[
                              const SizedBox(height: 14),
                              CyberFrame(
                                accent: _accent,
                                child: Text(
                                  'CORE ' +
                                      (_coreVersion.isEmpty ? 'unknown' : _coreVersion) +
                                      '  •  ROUTE ' +
                                      _settings.routingMode.toUpperCase() +
                                      '  •  DNS ' +
                                      _settings.dns +
                                      '\n' +
                                      (_coreError.isEmpty
                                          ? 'No native core error reported.'
                                          : 'Last error: ' + _coreError),
                                  style: const TextStyle(
                                    color: CyberPalette.muted,
                                    fontSize: 10,
                                    height: 1.45,
                                  ),
                                ),
                              ),
                            ],
                          ],
                        ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _TopBar extends StatelessWidget {
  const _TopBar({
    required this.onSettings,
    required this.onAdd,
  });

  final VoidCallback onSettings;
  final VoidCallback onAdd;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 8, 14, 4),
      child: Row(
        children: [
          IconButton(
            onPressed: onSettings,
            icon: const Icon(Icons.settings_outlined),
            color: CyberPalette.red,
          ),
          const Spacer(),
          const Text(
            'DarkXray',
            style: TextStyle(
              color: CyberPalette.text,
              fontSize: 19,
              fontWeight: FontWeight.w900,
              letterSpacing: 2.5,
            ),
          ),
          const Spacer(),
          IconButton(
            onPressed: onAdd,
            icon: const Icon(Icons.add_rounded, size: 31),
            color: CyberPalette.red,
          ),
        ],
      ),
    );
  }
}

class _PowerSection extends StatelessWidget {
  const _PowerSection({
    required this.connected,
    required this.reconnecting,
    required this.busy,
    required this.accent,
    required this.animation,
    required this.error,
    required this.onTap,
  });

  final bool connected;
  final bool reconnecting;
  final bool busy;
  final Color accent;
  final Animation<double> animation;
  final String error;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        AnimatedBuilder(
          animation: animation,
          builder: (context, _) {
            final glow = 13 + (animation.value * 16);
            return GestureDetector(
              onTap: busy ? null : onTap,
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 280),
                width: 178,
                height: 178,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: const RadialGradient(
                    colors: [
                      Color(0xFF141820),
                      Color(0xFF080A0E),
                    ],
                  ),
                  border: Border.all(color: accent, width: 2.2),
                  boxShadow: [
                    BoxShadow(
                      color: accent.withValues(alpha: 0.34),
                      blurRadius: glow,
                      spreadRadius: 2,
                    ),
                  ],
                ),
                child: Center(
                  child: busy
                      ? CircularProgressIndicator(color: accent)
                      : Icon(
                          Icons.power_settings_new_rounded,
                          size: 70,
                          color: accent,
                          shadows: [
                            Shadow(
                              color: accent.withValues(alpha: 0.8),
                              blurRadius: 20,
                            ),
                          ],
                        ),
                ),
              ),
            );
          },
        ),
        const SizedBox(height: 14),
        Text(
          busy
              ? 'CONNECTING'
              : reconnecting
                  ? 'RECONNECTING'
                  : connected
                      ? 'CONNECTED'
                      : error.isNotEmpty
                          ? 'CONNECTION ERROR'
                          : 'READY',
          style: TextStyle(
            color: accent,
            fontSize: 14,
            fontWeight: FontWeight.w900,
            letterSpacing: 2.2,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          reconnecting
              ? 'Waiting for the underlying network'
              : connected
                  ? 'Secure tunnel active'
                  : error.isNotEmpty
                      ? 'Open diagnostics or retry the connection'
                      : 'Tap power to connect',
          style: const TextStyle(
            color: CyberPalette.muted,
            fontSize: 12,
          ),
        ),
      ],
    );
  }
}

class _SessionStats extends StatelessWidget {
  const _SessionStats({
    required this.status,
    required this.accent,
  });

  final VpnStatus? status;
  final Color accent;

  String _formatBytes(int value) {
    if (value < 1024) return value.toString() + ' B';
    final kb = value / 1024;
    if (kb < 1024) return kb.toStringAsFixed(kb < 10 ? 1 : 0) + ' KB';
    final mb = kb / 1024;
    if (mb < 1024) return mb.toStringAsFixed(mb < 10 ? 1 : 0) + ' MB';
    final gb = mb / 1024;
    return gb.toStringAsFixed(gb < 10 ? 2 : 1) + ' GB';
  }

  String _formatDuration(Duration value) {
    final hours = value.inHours;
    final minutes = value.inMinutes.remainder(60);
    final seconds = value.inSeconds.remainder(60);
    if (hours > 0) {
      return hours.toString().padLeft(2, '0') +
          ':' +
          minutes.toString().padLeft(2, '0') +
          ':' +
          seconds.toString().padLeft(2, '0');
    }
    return minutes.toString().padLeft(2, '0') +
        ':' +
        seconds.toString().padLeft(2, '0');
  }

  @override
  Widget build(BuildContext context) {
    final current = status;
    return CyberFrame(
      accent: accent,
      child: Row(
        children: [
          Expanded(
            child: _Stat(
              label: 'SESSION',
              value: _formatDuration(
                current?.connectedDuration ?? Duration.zero,
              ),
              accent: accent,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: _Stat(
              label: 'DOWNLOAD',
              value: _formatBytes(current?.rxBytes ?? 0),
              accent: accent,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: _Stat(
              label: 'UPLOAD',
              value: _formatBytes(current?.txBytes ?? 0),
              accent: accent,
            ),
          ),
        ],
      ),
    );
  }
}

class _CoreErrorBanner extends StatelessWidget {
  const _CoreErrorBanner({required this.error});

  final String error;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: CyberPalette.red.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: CyberPalette.red.withValues(alpha: 0.55),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(
            Icons.error_outline_rounded,
            color: CyberPalette.red,
            size: 19,
          ),
          const SizedBox(width: 9),
          Expanded(
            child: Text(
              error,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                color: CyberPalette.text,
                fontSize: 11,
                height: 1.35,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _SubscriptionHeader extends StatelessWidget {
  const _SubscriptionHeader({
    required this.accent,
    required this.subtitle,
    required this.onTap,
  });

  final Color accent;
  final String subtitle;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Row(
        children: [
          Container(
            width: 46,
            height: 46,
            decoration: BoxDecoration(
              color: accent.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(13),
              border: Border.all(color: accent.withValues(alpha: 0.5)),
            ),
            child: Icon(Icons.layers_rounded, color: accent),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'DARKXRAY NETWORK',
                  style: TextStyle(
                    color: CyberPalette.text,
                    fontSize: 15,
                    fontWeight: FontWeight.w800,
                    letterSpacing: 0.6,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  subtitle,
                  style: const TextStyle(
                    color: CyberPalette.muted,
                    fontSize: 11,
                  ),
                ),
              ],
            ),
          ),
          Icon(Icons.chevron_right_rounded, color: accent),
        ],
      ),
    );
  }
}

class _StatsRow extends StatelessWidget {
  const _StatsRow({
    required this.profileCount,
    required this.selected,
    required this.accent,
  });

  final int profileCount;
  final ProxyProfile? selected;
  final Color accent;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: _Stat(
            label: 'PROFILES',
            value: profileCount.toString(),
            accent: accent,
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: _Stat(
            label: 'PROTOCOL',
            value: selected?.scheme.toUpperCase() ?? '—',
            accent: accent,
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: _Stat(
            label: 'PING',
            value: selected?.ping ?? '—',
            accent: accent,
          ),
        ),
      ],
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({
    required this.label,
    required this.value,
    required this.accent,
  });

  final String label;
  final String value;
  final Color accent;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 9, horizontal: 6),
      decoration: BoxDecoration(
        color: CyberPalette.panelSoft,
        borderRadius: BorderRadius.circular(9),
        border: Border.all(color: CyberPalette.line),
      ),
      child: Column(
        children: [
          Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              color: accent,
              fontSize: 12,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 3),
          Text(
            label,
            style: const TextStyle(
              color: CyberPalette.muted,
              fontSize: 9,
            ),
          ),
        ],
      ),
    );
  }
}

class _EmptyProfiles extends StatelessWidget {
  const _EmptyProfiles({
    required this.onAdd,
    required this.accent,
  });

  final VoidCallback onAdd;
  final Color accent;

  @override
  Widget build(BuildContext context) {
    return CyberFrame(
      accent: accent,
      child: Column(
        children: [
          Icon(Icons.shield_outlined, size: 38, color: accent),
          const SizedBox(height: 10),
          const Text(
            'NO CONFIGURATIONS',
            style: TextStyle(
              color: CyberPalette.text,
              fontWeight: FontWeight.w800,
              letterSpacing: 1,
            ),
          ),
          const SizedBox(height: 5),
          const Text(
            'Import a subscription, scan a QR code, paste a link or open a config file.',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: CyberPalette.muted,
              fontSize: 11,
              height: 1.4,
            ),
          ),
          const SizedBox(height: 12),
          CyberActionButton(
            label: 'ADD CONFIGURATION',
            icon: Icons.add_rounded,
            accent: accent,
            onTap: onAdd,
          ),
        ],
      ),
    );
  }
}

class _ProfileTile extends StatelessWidget {
  const _ProfileTile({
    required this.profile,
    required this.selected,
    required this.accent,
    required this.onTap,
  });

  final ProxyProfile profile;
  final bool selected;
  final Color accent;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 7),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(10),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 180),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              color: selected
                  ? accent.withValues(alpha: 0.10)
                  : CyberPalette.panelSoft,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(
                color: selected
                    ? accent.withValues(alpha: 0.65)
                    : CyberPalette.line,
              ),
            ),
            child: Row(
              children: [
                Container(
                  width: 36,
                  height: 36,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(9),
                    color: accent.withValues(alpha: 0.12),
                    border: Border.all(
                      color: accent.withValues(alpha: 0.38),
                    ),
                  ),
                  child: Icon(
                    profile.isDirect
                        ? Icons.public_rounded
                        : Icons.shield_outlined,
                    color: accent,
                    size: 19,
                  ),
                ),
                const SizedBox(width: 11),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        profile.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: CyberPalette.text,
                          fontSize: 12,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        profile.detail,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: CyberPalette.muted,
                          fontSize: 10,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  profile.ping,
                  style: TextStyle(
                    color: profile.ping == 'n/a' || profile.ping == 'timeout'
                        ? CyberPalette.muted
                        : accent,
                    fontSize: 10,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(width: 4),
                Icon(
                  selected
                      ? Icons.radio_button_checked_rounded
                      : Icons.radio_button_unchecked_rounded,
                  color: selected ? accent : CyberPalette.muted,
                  size: 18,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
