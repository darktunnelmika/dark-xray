import 'package:dark_xray_client/services/play_security.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('Google Play endpoint policy', () {
    test('allows encrypted VLESS Reality', () {
      expect(
        PlaySecurity.validateEncryptedEndpoint(
          'vless://id@example.com:443?security=reality&type=grpc#test',
        ),
        isNull,
      );
    });

    test('allows encrypted VLESS TLS', () {
      expect(
        PlaySecurity.validateEncryptedEndpoint(
          'vless://id@example.com:443?security=tls&type=ws#test',
        ),
        isNull,
      );
    });

    test('blocks plain VLESS', () {
      expect(
        PlaySecurity.validateEncryptedEndpoint(
          'vless://id@example.com:80?security=none&type=tcp#test',
        ),
        isNotNull,
      );
    });

    test('blocks plain SOCKS', () {
      expect(
        PlaySecurity.validateEncryptedEndpoint('socks://user:pass@example.com:1080'),
        isNotNull,
      );
    });

    test('allows Trojan, VMess, Shadowsocks and Hysteria2 families', () {
      expect(
        PlaySecurity.validateEncryptedEndpoint(
          'trojan://pass@example.com:443#test',
        ),
        isNull,
      );
      expect(
        PlaySecurity.validateEncryptedEndpoint('vmess://opaque'),
        isNull,
      );
      expect(
        PlaySecurity.validateEncryptedEndpoint('ss://opaque'),
        isNull,
      );
      expect(
        PlaySecurity.validateEncryptedEndpoint(
          'hysteria2://pass@example.com:443#test',
        ),
        isNull,
      );
    });
  });
}
