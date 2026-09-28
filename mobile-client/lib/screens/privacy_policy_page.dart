import 'package:flutter/material.dart';

import '../ui/cyber.dart';

class PrivacyPolicyPage extends StatelessWidget {
  const PrivacyPolicyPage({super.key});

  static const publicUrl =
      'https://darktunnelmika.github.io/dark-xray/privacy/';

  @override
  Widget build(BuildContext context) {
    return const CyberPage(
      title: 'PRIVACY POLICY',
      child: CyberFrame(
        child: SelectableText(
          'DarkXray Privacy Policy\n\n'
          'Effective date: September 28, 2026\n\n'
          'DarkXray is a VPN client. The app creates a device-level VPN tunnel '
          'only after you explicitly approve Android\'s VPN permission and start '
          'a connection.\n\n'
          'Data handled by the app\n'
          '• Proxy/VPN configuration links and subscription URLs you provide.\n'
          '• Network traffic required to provide the VPN connection.\n'
          '• Connection diagnostics such as latency, connection state, session '
          'duration, and app-level upload/download counters.\n\n'
          'Collection and sharing\n'
          'The current DarkXray mobile client does not include advertising SDKs, '
          'analytics SDKs, account sign-in, or telemetry sent to a DarkXray '
          'analytics service. Configuration data and app settings are stored '
          'locally on the device. Subscription requests are sent directly to the '
          'subscription URL you provide. VPN traffic is sent through the VPN or '
          'proxy endpoint selected by you.\n\n'
          'VPN traffic\n'
          'DarkXray uses Android VpnService because VPN connectivity is the core '
          'functionality of the app. DarkXray does not redirect or manipulate '
          'traffic for advertising or monetization. Play builds reject connection '
          'profiles that do not provide an encrypted path to the VPN/proxy '
          'endpoint.\n\n'
          'Retention and deletion\n'
          'Locally stored profiles, subscription URLs, settings, and diagnostics '
          'remain on the device until you remove them, reset local data, clear app '
          'storage, or uninstall DarkXray. The app provides Reset Local Data in '
          'System Settings.\n\n'
          'Security\n'
          'DarkXray uses Android app-private storage, Android VpnService, and the '
          'XTLS Xray core. Cleartext HTTP subscription URLs are not permitted in '
          'the Google Play build.\n\n'
          'Third-party endpoints\n'
          'VPN servers and subscription providers are independently operated '
          'endpoints. Their logging and retention practices are controlled by '
          'their operators. Review the policy of the provider whose endpoint you '
          'choose.\n\n'
          'Contact\n'
          'Privacy inquiries: https://github.com/darktunnelmika/dark-xray/issues/new\n\n'
          'Public policy URL:\n'
          'https://darktunnelmika.github.io/dark-xray/privacy/',
          style: TextStyle(
            color: CyberPalette.text,
            fontSize: 12,
            height: 1.55,
          ),
        ),
      ),
    );
  }
}
