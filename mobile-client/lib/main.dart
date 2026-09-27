import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'models/proxy_profile.dart';
import 'screens/subscriptions_page.dart';
import 'services/profile_store.dart';

void main() {
  runApp(const DarkXrayApp());
}

class DarkXrayApp extends StatelessWidget {
  const DarkXrayApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'DarkXray',
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF030406),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFFFF1744),
          secondary: Color(0xFF00F5FF),
          surface: Color(0xFF090C11),
        ),
        fontFamily: 'Roboto',
        useMaterial3: true,
      ),
      home: const HomePage(),
    );
  }
}

class CyberPalette {
  static const bg = Color(0xFF030406);
  static const panel = Color(0xFF090C11);
  static const panelSoft = Color(0xFF0D1118);
  static const red = Color(0xFFFF1744);
  static const redSoft = Color(0xFF7C0E24);
  static const cyan = Color(0xFF00F5FF);
  static const cyanSoft = Color(0xFF006F78);
  static const text = Color(0xFFF5F7FA);
  static const muted = Color(0xFF8B96A5);
  static const line = Color(0xFF202834);
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage>
    with SingleTickerProviderStateMixin {
  bool _connected = false;
  late final AnimationController _pulse;
  int _selectedProfile = 0;
  final _store = ProfileStore();
  String _subscriptionMeta = 'Demo profiles • tap to import';

  List<ProxyProfile> _profiles = const [
    ProxyProfile(
      id: 'demo-red-wraith',
      name: 'RED WRAITH · TUNNEL',
      scheme: 'vless',
      detail: 'VLESS | gRPC | Reality',
      rawUri: '',
      ping: '28 ms',
    ),
    ProxyProfile(
      id: 'demo-void-phantom',
      name: 'VOID PHANTOM · TUNNEL',
      scheme: 'vless',
      detail: 'VLESS | gRPC | Reality',
      rawUri: '',
      ping: '43 ms',
    ),
    ProxyProfile(
      id: 'demo-nightfall',
      name: 'NIGHTFALL · DIRECT',
      scheme: 'vless',
      detail: 'VLESS | TCP | Reality',
      rawUri: '',
    ),
    ProxyProfile(
      id: 'demo-crimson-veil',
      name: 'CRIMSON VEIL · TUNNEL',
      scheme: 'vless',
      detail: 'VLESS | gRPC | Reality',
      rawUri: '',
    ),
  ];

  @override
  void initState() {
    super.initState();
    _pulse = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1800),
      lowerBound: 0.0,
      upperBound: 1.0,
    )..repeat(reverse: true);
    _loadStoredProfiles();
  }

  Future<void> _loadStoredProfiles() async {
    final profiles = await _store.loadProfiles();
    if (!mounted || profiles.isEmpty) return;
    setState(() {
      _profiles = profiles;
      _selectedProfile = 0;
      _subscriptionMeta = '${profiles.length} profiles • stored on device';
    });
  }

  Future<void> _applyImportedProfiles(List<ProxyProfile>? profiles) async {
    if (!mounted || profiles == null || profiles.isEmpty) return;
    setState(() {
      _profiles = profiles;
      _selectedProfile = 0;
      _subscriptionMeta = '${profiles.length} profiles • synced now';
    });
  }

  Future<void> _openSubscriptions() async {
    final profiles = await Navigator.of(context).push<List<ProxyProfile>>(
      MaterialPageRoute(builder: (_) => const SubscriptionsPage()),
    );
    await _applyImportedProfiles(profiles);
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  Color get _accent => _connected ? CyberPalette.cyan : CyberPalette.red;

  Future<void> _openAddConfig() async {
    final profiles = await Navigator.of(context).push<List<ProxyProfile>>(
      MaterialPageRoute(builder: (_) => const AddConfigurationPage()),
    );
    await _applyImportedProfiles(profiles);
  }

  void _openSettings() {
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const SettingsPage()),
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
                const SizedBox(height: 8),
                Expanded(
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(18, 4, 18, 22),
                    children: [
                      _PowerSection(
                        connected: _connected,
                        accent: _accent,
                        animation: _pulse,
                        onTap: () => setState(() => _connected = !_connected),
                      ),
                      const SizedBox(height: 20),
                      CyberFrame(
                        accent: _accent,
                        child: Column(
                          children: [
                            _SubscriptionHeader(
                              accent: _accent,
                              subtitle: _subscriptionMeta,
                              onTap: _openSubscriptions,
                            ),
                            const SizedBox(height: 12),
                            _UsageBar(accent: _accent),
                            const SizedBox(height: 12),
                            Row(
                              children: [
                                Expanded(
                                  child: _CyberButton(
                                    label: 'PING',
                                    icon: Icons.speed_rounded,
                                    accent: _accent,
                                    onTap: () {},
                                  ),
                                ),
                                const SizedBox(width: 10),
                                _MiniButton(
                                  icon: Icons.more_horiz_rounded,
                                  accent: _accent,
                                  onTap: () {},
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 14),
                      CyberFrame(
                        accent: _accent,
                        child: Column(
                          children: [
                            for (var i = 0; i < _profiles.length; i++)
                              _ProfileTile(
                                profile: _profiles[i],
                                selected: _selectedProfile == i,
                                accent: _accent,
                                onTap: () =>
                                    setState(() => _selectedProfile = i),
                              ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 14),
                      Row(
                        children: [
                          Expanded(
                            child: _CyberButton(
                              label: 'CLIPBOARD',
                              icon: Icons.content_paste_rounded,
                              accent: _accent,
                              onTap: _openAddConfig,
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: _CyberButton(
                              label: 'QR SCAN',
                              icon: Icons.qr_code_scanner_rounded,
                              accent: _accent,
                              onTap: _openAddConfig,
                            ),
                          ),
                        ],
                      ),
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
  const _TopBar({required this.onSettings, required this.onAdd});

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
          RichText(
            text: const TextSpan(
              style: TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.w800,
                letterSpacing: 4.2,
              ),
              children: [
                TextSpan(
                  text: 'DARK',
                  style: TextStyle(color: CyberPalette.text),
                ),
                TextSpan(
                  text: 'X',
                  style: TextStyle(color: CyberPalette.red),
                ),
                TextSpan(
                  text: 'RAY',
                  style: TextStyle(color: CyberPalette.text),
                ),
              ],
            ),
          ),
          const Spacer(),
          IconButton(
            onPressed: onAdd,
            icon: const Icon(Icons.add_rounded, size: 32),
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
    required this.accent,
    required this.animation,
    required this.onTap,
  });

  final bool connected;
  final Color accent;
  final Animation<double> animation;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        AnimatedBuilder(
          animation: animation,
          builder: (context, child) {
            final glow = 12 + (animation.value * 18);
            return GestureDetector(
              onTap: onTap,
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 300),
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
                    BoxShadow(
                      color: accent.withValues(alpha: 0.14),
                      blurRadius: glow * 2.1,
                      spreadRadius: 5,
                    ),
                  ],
                ),
                child: Stack(
                  alignment: Alignment.center,
                  children: [
                    Container(
                      width: 138,
                      height: 138,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        border: Border.all(
                          color: accent.withValues(alpha: 0.45),
                        ),
                      ),
                    ),
                    Icon(
                      Icons.power_settings_new_rounded,
                      size: 68,
                      color: accent,
                      shadows: [
                        Shadow(
                          color: accent.withValues(alpha: 0.8),
                          blurRadius: 20,
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            );
          },
        ),
        const SizedBox(height: 14),
        AnimatedDefaultTextStyle(
          duration: const Duration(milliseconds: 250),
          style: TextStyle(
            color: accent,
            fontSize: 14,
            fontWeight: FontWeight.w800,
            letterSpacing: 2.2,
            shadows: [
              Shadow(
                color: accent.withValues(alpha: 0.55),
                blurRadius: 10,
              ),
            ],
          ),
          child: Text(connected ? 'CONNECTED' : 'READY'),
        ),
        const SizedBox(height: 5),
        Text(
          connected ? 'Secure tunnel active' : 'Tap power to connect',
          style: const TextStyle(
            color: CyberPalette.muted,
            fontSize: 12,
          ),
        ),
      ],
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

class _UsageBar extends StatelessWidget {
  const _UsageBar({required this.accent});

  final Color accent;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Row(
          children: [
            Icon(Icons.info_outline_rounded,
                color: CyberPalette.muted, size: 16),
            const SizedBox(width: 8),
            const Expanded(
              child: Text(
                '27 GB / ∞',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: CyberPalette.text,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 5),
        Container(
          height: 8,
          decoration: BoxDecoration(
            color: const Color(0xFF11151C),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: CyberPalette.line),
          ),
          alignment: Alignment.centerLeft,
          child: FractionallySizedBox(
            widthFactor: 0.34,
            child: Container(
              decoration: BoxDecoration(
                color: accent,
                borderRadius: BorderRadius.circular(20),
                boxShadow: [
                  BoxShadow(
                    color: accent.withValues(alpha: 0.45),
                    blurRadius: 8,
                  ),
                ],
              ),
            ),
          ),
        ),
      ],
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
                  width: 35,
                  height: 35,
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
                        style: const TextStyle(
                          color: CyberPalette.text,
                          fontSize: 12,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        profile.detail,
                        style: const TextStyle(
                          color: CyberPalette.muted,
                          fontSize: 10,
                        ),
                      ),
                    ],
                  ),
                ),
                Text(
                  profile.ping,
                  style: TextStyle(
                    color: profile.ping == 'n/a'
                        ? CyberPalette.muted
                        : accent,
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(width: 4),
                const Icon(
                  Icons.chevron_right_rounded,
                  size: 18,
                  color: CyberPalette.muted,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class CyberFrame extends StatelessWidget {
  const CyberFrame({
    super.key,
    required this.child,
    required this.accent,
    this.padding = const EdgeInsets.all(14),
  });

  final Widget child;
  final Color accent;
  final EdgeInsets padding;

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: _CyberFramePainter(accent),
      child: Container(
        width: double.infinity,
        padding: padding,
        child: child,
      ),
    );
  }
}

class _CyberFramePainter extends CustomPainter {
  _CyberFramePainter(this.accent);

  final Color accent;

  @override
  void paint(Canvas canvas, Size size) {
    final fill = Paint()
      ..color = CyberPalette.panel.withValues(alpha: 0.96)
      ..style = PaintingStyle.fill;
    final stroke = Paint()
      ..color = accent.withValues(alpha: 0.62)
      ..strokeWidth = 1.4
      ..style = PaintingStyle.stroke;

    const cut = 16.0;
    final path = Path()
      ..moveTo(cut, 0)
      ..lineTo(size.width - cut, 0)
      ..lineTo(size.width, cut)
      ..lineTo(size.width, size.height - cut)
      ..lineTo(size.width - cut, size.height)
      ..lineTo(cut, size.height)
      ..lineTo(0, size.height - cut)
      ..lineTo(0, cut)
      ..close();

    canvas.drawPath(path, fill);
    canvas.drawPath(path, stroke);

    final corner = Paint()
      ..color = accent
      ..strokeWidth = 2.2
      ..style = PaintingStyle.stroke;

    canvas.drawLine(const Offset(0, 28), const Offset(0, 13), corner);
    canvas.drawLine(const Offset(0, 13), const Offset(13, 0), corner);
    canvas.drawLine(
      Offset(size.width - 13, 0),
      Offset(size.width, 13),
      corner,
    );
    canvas.drawLine(
      Offset(size.width, 13),
      Offset(size.width, 28),
      corner,
    );
  }

  @override
  bool shouldRepaint(covariant _CyberFramePainter oldDelegate) =>
      oldDelegate.accent != accent;
}

class _CyberButton extends StatelessWidget {
  const _CyberButton({
    required this.label,
    required this.icon,
    required this.accent,
    required this.onTap,
  });

  final String label;
  final IconData icon;
  final Color accent;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(9),
        child: Ink(
          padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 12),
          decoration: BoxDecoration(
            color: CyberPalette.panelSoft,
            borderRadius: BorderRadius.circular(9),
            border: Border.all(color: accent.withValues(alpha: 0.62)),
            boxShadow: [
              BoxShadow(
                color: accent.withValues(alpha: 0.10),
                blurRadius: 10,
              ),
            ],
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icon, size: 18, color: accent),
              const SizedBox(width: 8),
              Text(
                label,
                style: const TextStyle(
                  color: CyberPalette.text,
                  fontSize: 11,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 0.6,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _MiniButton extends StatelessWidget {
  const _MiniButton({
    required this.icon,
    required this.accent,
    required this.onTap,
  });

  final IconData icon;
  final Color accent;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return IconButton(
      onPressed: onTap,
      icon: Icon(icon, color: accent),
      style: IconButton.styleFrom(
        backgroundColor: CyberPalette.panelSoft,
        side: BorderSide(color: accent.withValues(alpha: 0.6)),
      ),
    );
  }
}

class CyberBackground extends StatelessWidget {
  const CyberBackground({super.key});

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: const _CyberBackgroundPainter(),
      child: const SizedBox.expand(),
    );
  }
}

class _CyberBackgroundPainter extends CustomPainter {
  const _CyberBackgroundPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final bg = Paint()..color = CyberPalette.bg;
    canvas.drawRect(Offset.zero & size, bg);

    final grid = Paint()
      ..color = CyberPalette.red.withValues(alpha: 0.035)
      ..strokeWidth = 0.8;
    const gap = 26.0;
    for (double x = 0; x < size.width; x += gap) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), grid);
    }
    for (double y = 0; y < size.height; y += gap) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }

    final arc = Paint()
      ..color = CyberPalette.red.withValues(alpha: 0.08)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2;
    for (var i = 0; i < 4; i++) {
      final radius = 170.0 + (i * 62);
      canvas.drawArc(
        Rect.fromCircle(
          center: Offset(size.width / 2, 250),
          radius: radius,
        ),
        math.pi * 0.12,
        math.pi * 0.76,
        false,
        arc,
      );
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class AddConfigurationPage extends StatelessWidget {
  const AddConfigurationPage({super.key});

  @override
  Widget build(BuildContext context) {
    return _CyberPage(
      title: 'ADD CONFIGURATION',
      child: CyberFrame(
        accent: CyberPalette.red,
        child: Column(
          children: [
            _MenuTile(
              icon: Icons.content_paste_rounded,
              title: 'Paste Link',
              subtitle: 'Import subscription URL',
              onTap: () async {
                final profiles =
                    await Navigator.of(context).push<List<ProxyProfile>>(
                  MaterialPageRoute(
                    builder: (_) => const SubscriptionsPage(),
                  ),
                );
                if (context.mounted && profiles != null && profiles.isNotEmpty) {
                  Navigator.of(context).pop<List<ProxyProfile>>(profiles);
                }
              },
            ),
            _MenuTile(
              icon: Icons.qr_code_scanner_rounded,
              title: 'Scan QR Code',
              subtitle: 'Open camera scanner',
              onTap: () {},
            ),
            _MenuTile(
              icon: Icons.insert_drive_file_outlined,
              title: 'Import File',
              subtitle: 'Load local configuration',
              onTap: () {},
            ),
            _MenuTile(
              icon: Icons.tune_rounded,
              title: 'Manual Configuration',
              subtitle: 'Create profile manually',
              onTap: () {},
            ),
          ],
        ),
      ),
    );
  }
}

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return _CyberPage(
      title: 'SYSTEM SETTINGS',
      child: Column(
        children: [
          CyberFrame(
            accent: CyberPalette.red,
            child: Column(
              children: [
                _ToggleTile(
                  icon: Icons.developer_mode_rounded,
                  title: 'Developer Mode',
                  value: false,
                ),
                _MenuTile(
                  icon: Icons.layers_outlined,
                  title: 'Sources',
                  onTap: () {},
                ),
                _MenuTile(
                  icon: Icons.speed_rounded,
                  title: 'Ping',
                  onTap: () {},
                ),
                _ToggleTile(
                  icon: Icons.router_outlined,
                  title: 'Allow LAN Connections',
                  value: true,
                ),
              ],
            ),
          ),
          const SizedBox(height: 14),
          CyberFrame(
            accent: CyberPalette.red,
            child: Column(
              children: [
                _MenuTile(
                  icon: Icons.translate_rounded,
                  title: 'Language',
                  subtitle: 'English',
                  onTap: () {},
                ),
                _MenuTile(
                  icon: Icons.text_fields_rounded,
                  title: 'Font Size',
                  subtitle: 'Normal',
                  onTap: () {},
                ),
                _MenuTile(
                  icon: Icons.palette_outlined,
                  title: 'App Appearance',
                  subtitle: 'DarkXray Black',
                  onTap: () {},
                ),
                _MenuTile(
                  icon: Icons.alt_route_rounded,
                  title: 'Routing Rules',
                  onTap: () {
                    Navigator.of(context).push(
                      MaterialPageRoute(
                        builder: (_) => const RoutingRulesPage(),
                      ),
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

class RoutingRulesPage extends StatelessWidget {
  const RoutingRulesPage({super.key});

  @override
  Widget build(BuildContext context) {
    return _CyberPage(
      title: 'ROUTING RULES',
      child: Column(
        children: [
          const _SegmentBar(),
          const SizedBox(height: 14),
          CyberFrame(
            accent: CyberPalette.red,
            child: Column(
              children: [
                _RouteTile('Messaging', 'PROXY', Icons.chat_bubble_outline),
                _RouteTile('Local Sites', 'DIRECT', Icons.desktop_windows_outlined),
                _RouteTile('Private LAN', 'BYPASS', Icons.home_outlined),
                _RouteTile('Global DNS', 'PROXY', Icons.language_rounded),
              ],
            ),
          ),
          const SizedBox(height: 14),
          _CyberButton(
            label: 'ADD RULE',
            icon: Icons.add_rounded,
            accent: CyberPalette.red,
            onTap: () {},
          ),
        ],
      ),
    );
  }
}

class _CyberPage extends StatelessWidget {
  const _CyberPage({required this.title, required this.child});

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Stack(
        children: [
          const Positioned.fill(child: CyberBackground()),
          SafeArea(
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(8, 6, 14, 8),
                  child: Row(
                    children: [
                      IconButton(
                        onPressed: () => Navigator.maybePop(context),
                        icon: const Icon(Icons.arrow_back_ios_new_rounded),
                        color: CyberPalette.red,
                      ),
                      Expanded(
                        child: Text(
                          title,
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            color: CyberPalette.text,
                            fontSize: 15,
                            fontWeight: FontWeight.w800,
                            letterSpacing: 1.6,
                          ),
                        ),
                      ),
                      const SizedBox(width: 48),
                    ],
                  ),
                ),
                Expanded(
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.fromLTRB(18, 4, 18, 24),
                    child: child,
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

class _MenuTile extends StatelessWidget {
  const _MenuTile({
    required this.icon,
    required this.title,
    this.subtitle,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String? subtitle;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(11),
          child: Ink(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
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
                      if (subtitle != null) ...[
                        const SizedBox(height: 2),
                        Text(
                          subtitle!,
                          style: const TextStyle(
                            color: CyberPalette.muted,
                            fontSize: 10,
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
                const Icon(
                  Icons.chevron_right_rounded,
                  color: CyberPalette.red,
                  size: 19,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _ToggleTile extends StatefulWidget {
  const _ToggleTile({
    required this.icon,
    required this.title,
    required this.value,
  });

  final IconData icon;
  final String title;
  final bool value;

  @override
  State<_ToggleTile> createState() => _ToggleTileState();
}

class _ToggleTileState extends State<_ToggleTile> {
  late bool value;

  @override
  void initState() {
    super.initState();
    value = widget.value;
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
        decoration: BoxDecoration(
          color: CyberPalette.panelSoft,
          borderRadius: BorderRadius.circular(11),
          border: Border.all(color: CyberPalette.line),
        ),
        child: Row(
          children: [
            Icon(widget.icon, color: CyberPalette.red, size: 21),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                widget.title,
                style: const TextStyle(
                  color: CyberPalette.text,
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
            Switch(
              value: value,
              activeThumbColor: CyberPalette.red,
              activeTrackColor: CyberPalette.redSoft,
              onChanged: (next) => setState(() => value = next),
            ),
          ],
        ),
      ),
    );
  }
}

class _SegmentBar extends StatelessWidget {
  const _SegmentBar();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: CyberPalette.panel,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: CyberPalette.line),
      ),
      child: Row(
        children: [
          Expanded(child: _segment('GLOBAL', true)),
          Expanded(child: _segment('RULE', false)),
          Expanded(child: _segment('DIRECT', false)),
        ],
      ),
    );
  }

  Widget _segment(String label, bool active) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 10),
      decoration: BoxDecoration(
        color: active ? CyberPalette.red : Colors.transparent,
        borderRadius: BorderRadius.circular(7),
      ),
      child: Text(
        label,
        textAlign: TextAlign.center,
        style: const TextStyle(
          color: CyberPalette.text,
          fontSize: 11,
          fontWeight: FontWeight.w800,
        ),
      ),
    );
  }
}

class _RouteTile extends StatelessWidget {
  const _RouteTile(this.title, this.target, this.icon);

  final String title;
  final String target;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final proxy = target == 'PROXY';
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
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
              child: Text(
                title,
                style: const TextStyle(
                  color: CyberPalette.text,
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
            Text(
              '→ $target',
              style: TextStyle(
                color: proxy ? CyberPalette.red : CyberPalette.muted,
                fontSize: 11,
                fontWeight: FontWeight.w800,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
