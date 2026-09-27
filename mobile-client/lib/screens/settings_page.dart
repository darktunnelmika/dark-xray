import 'package:flutter/material.dart';

import '../services/settings_store.dart';
import '../services/vpn_bridge.dart';
import '../ui/cyber.dart';
import 'routing_rules_page.dart';
import 'subscriptions_page.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final _store = SettingsStore();
  final _vpn = VpnBridge();
  DarkXraySettings _settings = const DarkXraySettings();
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final settings = await _store.load();
    if (!mounted) return;
    setState(() {
      _settings = settings;
      _loading = false;
    });
  }

  Future<void> _save(DarkXraySettings settings) async {
    await _store.save(settings);
    if (!mounted) return;
    setState(() => _settings = settings);
  }

  Future<void> _openSources() async {
    await Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const SubscriptionsPage()),
    );
  }

  Future<void> _choosePingTimeout() async {
    final selected = await showDialog<int>(
      context: context,
      builder: (context) => SimpleDialog(
        backgroundColor: CyberPalette.panel,
        title: const Text(
          'Ping timeout',
          style: TextStyle(color: CyberPalette.text),
        ),
        children: [
          for (final value in const [1500, 2500, 5000, 8000])
            SimpleDialogOption(
              onPressed: () => Navigator.pop(context, value),
              child: Text(
                '${(value / 1000).toStringAsFixed(value % 1000 == 0 ? 0 : 1)} seconds',
                style: TextStyle(
                  color: value == _settings.pingTimeoutMs
                      ? CyberPalette.red
                      : CyberPalette.text,
                ),
              ),
            ),
        ],
      ),
    );
    if (selected == null) return;
    await _save(_settings.copyWith(pingTimeoutMs: selected));
  }

  Future<void> _chooseAutoRefresh() async {
    final selected = await showDialog<int>(
      context: context,
      builder: (context) => SimpleDialog(
        backgroundColor: CyberPalette.panel,
        title: const Text(
          'Subscription auto refresh',
          style: TextStyle(color: CyberPalette.text),
        ),
        children: [
          for (final value in const [0, 6, 12, 24])
            SimpleDialogOption(
              onPressed: () => Navigator.pop(context, value),
              child: Text(
                value == 0 ? 'Off' : 'Every $value hours',
                style: TextStyle(
                  color: value == _settings.autoRefreshHours
                      ? CyberPalette.red
                      : CyberPalette.text,
                ),
              ),
            ),
        ],
      ),
    );
    if (selected == null) return;
    await _save(_settings.copyWith(autoRefreshHours: selected));
  }

  Future<void> _openKillSwitchSettings() async {
    try {
      await _vpn.openSystemVpnSettings();
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not open VPN settings: $error')),
      );
    }
  }

  Future<void> _openBatterySettings() async {
    try {
      await _vpn.openBatterySettings();
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not open battery settings: $error')),
      );
    }
  }

  Future<void> _editDns() async {
    final controller = TextEditingController(text: _settings.dns);
    final value = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: CyberPalette.panel,
        title: const Text(
          'VPN DNS server',
          style: TextStyle(color: CyberPalette.text),
        ),
        content: TextField(
          controller: controller,
          keyboardType: TextInputType.number,
          style: const TextStyle(color: CyberPalette.text),
          decoration: const InputDecoration(
            hintText: '1.1.1.1',
            hintStyle: TextStyle(color: CyberPalette.muted),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('CANCEL'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            style: FilledButton.styleFrom(
              backgroundColor: CyberPalette.red,
            ),
            child: const Text('SAVE'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (value == null || value.isEmpty) return;
    await _save(_settings.copyWith(dns: value));
  }

  Future<void> _showCoreStatus() async {
    try {
      final status = await _vpn.status();
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          backgroundColor: CyberPalette.panel,
          title: const Text(
            'Xray Core',
            style: TextStyle(color: CyberPalette.text),
          ),
          content: Text(
            'Status: ${status.running ? 'RUNNING' : status.reconnecting ? 'RECONNECTING' : 'STOPPED'}\n'
            'Wanted: ${status.desiredConnected ? 'CONNECTED' : 'DISCONNECTED'}\n'
            'Version: ${status.version.isEmpty ? 'unknown' : status.version}\n'
            'RX: ${status.rxBytes} bytes\n'
            'TX: ${status.txBytes} bytes\n'
            'Error: ${status.error.isEmpty ? 'none' : status.error}',
            style: const TextStyle(
              color: CyberPalette.muted,
              height: 1.55,
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
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Core status failed: $error')),
      );
    }
  }

  Future<void> _resetApp() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: CyberPalette.panel,
        title: const Text(
          'Reset DarkXray?',
          style: TextStyle(color: CyberPalette.text),
        ),
        content: const Text(
          'This removes saved subscriptions, profiles, routing and settings from this device.',
          style: TextStyle(color: CyberPalette.muted),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('CANCEL'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            style: FilledButton.styleFrom(backgroundColor: CyberPalette.red),
            child: const Text('RESET'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    await _store.clearAppState();
    if (!mounted) return;
    setState(() => _settings = const DarkXraySettings());
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('DarkXray local data was reset.')),
    );
  }

  @override
  Widget build(BuildContext context) {
    return CyberPage(
      title: 'SYSTEM SETTINGS',
      child: _loading
          ? const Center(
              child: CircularProgressIndicator(color: CyberPalette.red),
            )
          : Column(
              children: [
                CyberFrame(
                  child: Column(
                    children: [
                      _SwitchSetting(
                        icon: Icons.developer_mode_rounded,
                        title: 'Developer Mode',
                        subtitle: 'Keep extra diagnostics visible in the app',
                        value: _settings.developerMode,
                        onChanged: (value) =>
                            _save(_settings.copyWith(developerMode: value)),
                      ),
                      _SwitchSetting(
                        icon: Icons.autorenew_rounded,
                        title: 'Auto Reconnect',
                        subtitle:
                            'Reconnect after Wi-Fi/mobile changes or temporary network loss',
                        value: _settings.autoReconnect,
                        onChanged: (value) =>
                            _save(_settings.copyWith(autoReconnect: value)),
                      ),
                      _SwitchSetting(
                        icon: Icons.router_outlined,
                        title: 'Bypass Private LAN',
                        subtitle: 'Send local network ranges directly',
                        value: _settings.allowLan,
                        onChanged: (value) =>
                            _save(_settings.copyWith(allowLan: value)),
                      ),
                      CyberMenuTile(
                        icon: Icons.dns_outlined,
                        title: 'VPN DNS',
                        subtitle: _settings.dns,
                        onTap: _editDns,
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 14),
                CyberFrame(
                  child: Column(
                    children: [
                      CyberMenuTile(
                        icon: Icons.layers_outlined,
                        title: 'Subscriptions',
                        subtitle: 'Add or refresh source URLs',
                        onTap: _openSources,
                      ),
                      CyberMenuTile(
                        icon: Icons.sync_rounded,
                        title: 'Auto Refresh Subscription',
                        subtitle: _settings.autoRefreshHours == 0
                            ? 'Off'
                            : 'Every ${_settings.autoRefreshHours} hours',
                        onTap: _chooseAutoRefresh,
                      ),
                      CyberMenuTile(
                        icon: Icons.speed_rounded,
                        title: 'Ping Timeout',
                        subtitle: '${_settings.pingTimeoutMs} ms',
                        onTap: _choosePingTimeout,
                      ),
                      CyberMenuTile(
                        icon: Icons.alt_route_rounded,
                        title: 'Routing Mode',
                        subtitle: _settings.routingMode.toUpperCase(),
                        onTap: () async {
                          await Navigator.of(context).push(
                            MaterialPageRoute(
                              builder: (_) => const RoutingRulesPage(),
                            ),
                          );
                          await _load();
                        },
                      ),
                      CyberMenuTile(
                        icon: Icons.security_rounded,
                        title: 'System Kill Switch',
                        subtitle:
                            'Open Android Always-on VPN / Block without VPN settings',
                        onTap: _openKillSwitchSettings,
                      ),
                      CyberMenuTile(
                        icon: Icons.battery_saver_rounded,
                        title: 'Battery Optimization',
                        subtitle:
                            'Open Android battery optimization settings for background stability',
                        onTap: _openBatterySettings,
                      ),
                      CyberMenuTile(
                        icon: Icons.memory_rounded,
                        title: 'Xray Core Status',
                        subtitle: 'Version, running state and last error',
                        onTap: _showCoreStatus,
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 14),
                CyberFrame(
                  child: Column(
                    children: [
                      CyberMenuTile(
                        icon: Icons.delete_outline_rounded,
                        title: 'Reset Local Data',
                        subtitle: 'Remove profiles, subscriptions and settings',
                        onTap: _resetApp,
                      ),
                      CyberMenuTile(
                        icon: Icons.info_outline_rounded,
                        title: 'About DarkXray',
                        subtitle: 'DarkXray Android v0.5.0 Stability V1',
                        onTap: () {
                          showAboutDialog(
                            context: context,
                            applicationName: 'DarkXray',
                            applicationVersion: '0.5.0',
                            applicationLegalese:
                                'Dedicated Xray client • Cyber Red UI',
                          );
                        },
                      ),
                    ],
                  ),
                ),
              ],
            ),
    );
  }
}

class _SwitchSetting extends StatelessWidget {
  const _SwitchSetting({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Container(
        padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
        decoration: BoxDecoration(
          color: CyberPalette.panelSoft,
          borderRadius: BorderRadius.circular(11),
          border: Border.all(color: CyberPalette.line),
        ),
        child: Row(
          children: [
            Icon(icon, color: CyberPalette.red, size: 21),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: const TextStyle(
                      color: CyberPalette.text,
                      fontSize: 13,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    subtitle,
                    style: const TextStyle(
                      color: CyberPalette.muted,
                      fontSize: 10,
                    ),
                  ),
                ],
              ),
            ),
            Switch(
              value: value,
              activeThumbColor: CyberPalette.red,
              activeTrackColor: CyberPalette.redSoft,
              onChanged: onChanged,
            ),
          ],
        ),
      ),
    );
  }
}
