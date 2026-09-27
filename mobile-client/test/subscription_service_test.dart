import 'dart:convert';

import 'package:dark_xray_client/services/subscription_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  const service = SubscriptionService();

  test('parses base64 subscription with VLESS and Trojan', () {
    const vless =
        'vless://11111111-1111-1111-1111-111111111111@example.com:443?type=grpc&security=reality#Dark%20TR';
    const trojan =
        'trojan://secret@example.net:443?type=tcp&security=tls#Dark%20TLS';
    final encoded = base64Encode(utf8.encode('$vless\n$trojan'));

    final profiles = service.parsePayload(encoded);

    expect(profiles, hasLength(2));
    expect(profiles.first.name, 'Dark TR');
    expect(profiles.first.detail, contains('Reality'));
    expect(profiles.last.scheme, 'trojan');
  });

  test('parses VMess JSON profile', () {
    final vmessJson = jsonEncode({
      'v': '2',
      'ps': 'Dark VMess',
      'add': 'vmess.example.com',
      'port': '443',
      'id': '11111111-1111-1111-1111-111111111111',
      'net': 'ws',
      'tls': 'tls',
    });
    final uri = 'vmess://${base64Encode(utf8.encode(vmessJson))}';

    final profiles = service.parsePayload(uri);

    expect(profiles, hasLength(1));
    expect(profiles.first.name, 'Dark VMess');
    expect(profiles.first.detail, contains('WS'));
    expect(profiles.first.port, 443);
  });
}
