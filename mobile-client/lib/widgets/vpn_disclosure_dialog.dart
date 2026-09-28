import 'package:flutter/material.dart';

import '../ui/cyber.dart';

class VpnDisclosureDialog extends StatelessWidget {
  const VpnDisclosureDialog({super.key});

  static Future<bool> show(BuildContext context) async {
    final accepted = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (_) => const VpnDisclosureDialog(),
    );
    return accepted == true;
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      backgroundColor: CyberPalette.panel,
      title: const Row(
        children: [
          Icon(Icons.shield_outlined, color: CyberPalette.red),
          SizedBox(width: 10),
          Expanded(
            child: Text(
              'VPN CONNECTION DISCLOSURE',
              style: TextStyle(
                color: CyberPalette.text,
                fontSize: 15,
                fontWeight: FontWeight.w800,
              ),
            ),
          ),
        ],
      ),
      content: const SingleChildScrollView(
        child: Text(
          'DarkXray uses Android VpnService because creating a VPN tunnel is the '
          'core purpose of the app.\n\n'
          'When you connect, device network traffic is routed through the '
          'VPN/proxy endpoint you selected so the connection can function. '
          'DarkXray does not use VpnService to inject ads, redirect advertising '
          'traffic, or sell browsing activity.\n\n'
          'The current app does not send analytics or VPN browsing telemetry to '
          'a DarkXray analytics service. Your configuration and subscription '
          'details are stored locally on this device.\n\n'
          'By tapping I AGREE, you consent to DarkXray creating the VPN tunnel '
          'and processing network traffic only as required to provide the VPN '
          'connection.',
          style: TextStyle(
            color: CyberPalette.muted,
            fontSize: 12,
            height: 1.5,
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('CANCEL'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          style: FilledButton.styleFrom(
            backgroundColor: CyberPalette.red,
            foregroundColor: Colors.white,
          ),
          child: const Text('I AGREE'),
        ),
      ],
    );
  }
}
