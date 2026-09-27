import 'dart:math' as math;

import 'package:flutter/material.dart';

class CyberPalette {
  static const bg = Color(0xFF030406);
  static const panel = Color(0xFF090C11);
  static const panelSoft = Color(0xFF0D1118);
  static const red = Color(0xFFFF1744);
  static const redSoft = Color(0xFF7C0E24);
  static const cyan = Color(0xFF00F5FF);
  static const text = Color(0xFFF5F7FA);
  static const muted = Color(0xFF8B96A5);
  static const line = Color(0xFF202834);
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
    canvas.drawRect(
      Offset.zero & size,
      Paint()..color = CyberPalette.bg,
    );

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
      canvas.drawArc(
        Rect.fromCircle(
          center: Offset(size.width / 2, 245),
          radius: 165.0 + (i * 60),
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

class CyberFrame extends StatelessWidget {
  const CyberFrame({
    super.key,
    required this.child,
    this.accent = CyberPalette.red,
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
      ..color = CyberPalette.panel.withValues(alpha: 0.97)
      ..style = PaintingStyle.fill;
    final stroke = Paint()
      ..color = accent.withValues(alpha: 0.58)
      ..strokeWidth = 1.35
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
  }

  @override
  bool shouldRepaint(covariant _CyberFramePainter oldDelegate) =>
      oldDelegate.accent != accent;
}

class CyberPage extends StatelessWidget {
  const CyberPage({
    super.key,
    required this.title,
    required this.child,
  });

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

class CyberMenuTile extends StatelessWidget {
  const CyberMenuTile({
    super.key,
    required this.icon,
    required this.title,
    this.subtitle,
    required this.onTap,
    this.trailing,
  });

  final IconData icon;
  final String title;
  final String? subtitle;
  final VoidCallback onTap;
  final Widget? trailing;

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
                trailing ??
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

class CyberActionButton extends StatelessWidget {
  const CyberActionButton({
    super.key,
    required this.label,
    required this.icon,
    required this.onTap,
    this.accent = CyberPalette.red,
  });

  final String label;
  final IconData icon;
  final VoidCallback onTap;
  final Color accent;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(9),
        child: Ink(
          padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 10),
          decoration: BoxDecoration(
            color: CyberPalette.panelSoft,
            borderRadius: BorderRadius.circular(9),
            border: Border.all(color: accent.withValues(alpha: 0.62)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icon, size: 18, color: accent),
              const SizedBox(width: 8),
              Flexible(
                child: Text(
                  label,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: CyberPalette.text,
                    fontSize: 11,
                    fontWeight: FontWeight.w800,
                    letterSpacing: 0.5,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
