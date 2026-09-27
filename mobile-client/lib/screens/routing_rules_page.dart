import 'package:flutter/material.dart';

import '../services/settings_store.dart';
import '../ui/cyber.dart';

class RoutingRulesPage extends StatefulWidget {
  const RoutingRulesPage({super.key});

  @override
  State<RoutingRulesPage> createState() => _RoutingRulesPageState();
}

class _RoutingRulesPageState extends State<RoutingRulesPage> {
  final _store = SettingsStore();
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

  Future<void> _setMode(String mode) async {
    final updated = _settings.copyWith(routingMode: mode);
    await _store.save(updated);
    if (!mounted) return;
    setState(() => _settings = updated);
  }

  @override
  Widget build(BuildContext context) {
    return CyberPage(
      title: 'ROUTING MODE',
      child: _loading
          ? const Center(
              child: CircularProgressIndicator(color: CyberPalette.red),
            )
          : Column(
              children: [
                CyberFrame(
                  child: Column(
                    children: [
                      _ModeTile(
                        title: 'GLOBAL PROXY',
                        subtitle: 'Route all VPN traffic through the selected proxy.',
                        selected: _settings.routingMode == 'global',
                        onTap: () => _setMode('global'),
                      ),
                      const SizedBox(height: 10),
                      _ModeTile(
                        title: 'DIRECT TEST',
                        subtitle:
                            'Keep the VPN interface active but send traffic directly. Useful for diagnostics.',
                        selected: _settings.routingMode == 'direct',
                        onTap: () => _setMode('direct'),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 14),
                const CyberFrame(
                  child: Text(
                    'Private LAN bypass is controlled separately in System Settings. '
                    'The selected routing mode is used on the next VPN connection.',
                    style: TextStyle(
                      color: CyberPalette.muted,
                      fontSize: 12,
                      height: 1.45,
                    ),
                  ),
                ),
              ],
            ),
    );
  }
}

class _ModeTile extends StatelessWidget {
  const _ModeTile({
    required this.title,
    required this.subtitle,
    required this.selected,
    required this.onTap,
  });

  final String title;
  final String subtitle;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final accent = selected ? CyberPalette.red : CyberPalette.line;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(11),
        child: Ink(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: selected
                ? CyberPalette.red.withValues(alpha: 0.08)
                : CyberPalette.panelSoft,
            borderRadius: BorderRadius.circular(11),
            border: Border.all(color: accent),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                selected
                    ? Icons.radio_button_checked_rounded
                    : Icons.radio_button_unchecked_rounded,
                color: selected ? CyberPalette.red : CyberPalette.muted,
              ),
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
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      subtitle,
                      style: const TextStyle(
                        color: CyberPalette.muted,
                        fontSize: 11,
                        height: 1.4,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
