import 'package:flutter/material.dart';

void main() {
  runApp(const DarkXrayApp());
}

class DarkXrayApp extends StatelessWidget {
  const DarkXrayApp({super.key});

  @override
  Widget build(BuildContext context) {
    const accent = Color(0xFF00E5FF);
    const background = Color(0xFF05080D);

    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'DARK XRAY',
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: background,
        colorScheme: const ColorScheme.dark(
          primary: accent,
          secondary: Color(0xFF7C4DFF),
          surface: Color(0xFF0B111A),
        ),
        useMaterial3: true,
      ),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  bool _connected = false;
  String _server = 'Germany • 42 ms';

  @override
  Widget build(BuildContext context) {
    final accent = Theme.of(context).colorScheme.primary;
    final status = _connected ? 'CONNECTED' : 'DISCONNECTED';

    return Scaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text(
          'DARK XRAY',
          style: TextStyle(
            fontWeight: FontWeight.w800,
            letterSpacing: 2.2,
          ),
        ),
        actions: [
          IconButton(
            tooltip: 'Settings',
            onPressed: () {},
            icon: const Icon(Icons.tune_rounded),
          ),
        ],
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 20),
          child: Column(
            children: [
              _StatusCard(
                connected: _connected,
                status: status,
                server: _server,
              ),
              const Spacer(),
              GestureDetector(
                onTap: () => setState(() => _connected = !_connected),
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 260),
                  width: 190,
                  height: 190,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(color: accent, width: 2),
                    boxShadow: [
                      BoxShadow(
                        color: accent.withValues(alpha: _connected ? 0.32 : 0.14),
                        blurRadius: _connected ? 42 : 24,
                        spreadRadius: 2,
                      ),
                    ],
                    gradient: RadialGradient(
                      colors: [
                        accent.withValues(alpha: _connected ? 0.22 : 0.08),
                        const Color(0xFF071018),
                      ],
                    ),
                  ),
                  child: Icon(
                    Icons.power_settings_new_rounded,
                    size: 74,
                    color: accent,
                  ),
                ),
              ),
              const SizedBox(height: 26),
              Text(
                _connected ? 'Tap to disconnect' : 'Tap to connect',
                style: TextStyle(
                  color: Colors.white.withValues(alpha: 0.72),
                  fontSize: 14,
                  letterSpacing: 0.4,
                ),
              ),
              const Spacer(),
              _QuickActions(
                onSubscription: () {},
                onProfiles: () {},
                onServer: () {
                  setState(() {
                    _server = _server.startsWith('Germany')
                        ? 'Netherlands • 51 ms'
                        : 'Germany • 42 ms';
                  });
                },
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({
    required this.connected,
    required this.status,
    required this.server,
  });

  final bool connected;
  final String status;
  final String server;

  @override
  Widget build(BuildContext context) {
    final accent = Theme.of(context).colorScheme.primary;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: const Color(0xFF0A1018),
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: accent.withValues(alpha: 0.28)),
      ),
      child: Row(
        children: [
          Container(
            width: 11,
            height: 11,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: connected ? const Color(0xFF00FF9D) : const Color(0xFFFF4D6D),
              boxShadow: [
                BoxShadow(
                  color: connected
                      ? const Color(0xFF00FF9D).withValues(alpha: 0.45)
                      : const Color(0xFFFF4D6D).withValues(alpha: 0.35),
                  blurRadius: 12,
                ),
              ],
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  status,
                  style: const TextStyle(
                    fontWeight: FontWeight.w800,
                    letterSpacing: 1.2,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  server,
                  style: TextStyle(
                    color: Colors.white.withValues(alpha: 0.62),
                    fontSize: 13,
                  ),
                ),
              ],
            ),
          ),
          const Icon(Icons.shield_outlined),
        ],
      ),
    );
  }
}

class _QuickActions extends StatelessWidget {
  const _QuickActions({
    required this.onSubscription,
    required this.onProfiles,
    required this.onServer,
  });

  final VoidCallback onSubscription;
  final VoidCallback onProfiles;
  final VoidCallback onServer;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: _ActionButton(
            icon: Icons.link_rounded,
            label: 'Subscription',
            onTap: onSubscription,
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _ActionButton(
            icon: Icons.layers_outlined,
            label: 'Profiles',
            onTap: onProfiles,
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _ActionButton(
            icon: Icons.public_rounded,
            label: 'Server',
            onTap: onServer,
          ),
        ),
      ],
    );
  }
}

class _ActionButton extends StatelessWidget {
  const _ActionButton({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final accent = Theme.of(context).colorScheme.primary;

    return InkWell(
      borderRadius: BorderRadius.circular(18),
      onTap: onTap,
      child: Ink(
        padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 8),
        decoration: BoxDecoration(
          color: const Color(0xFF0A1018),
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: accent.withValues(alpha: 0.18)),
        ),
        child: Column(
          children: [
            Icon(icon, color: accent),
            const SizedBox(height: 7),
            Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12),
            ),
          ],
        ),
      ),
    );
  }
}
