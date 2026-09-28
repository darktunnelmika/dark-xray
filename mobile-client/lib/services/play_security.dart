class PlaySecurity {
  const PlaySecurity._();

  static String? validateEncryptedEndpoint(String rawUri) {
    final value = rawUri.trim();
    final lower = value.toLowerCase();

    if (lower.startsWith('socks://')) {
      return 'Google Play builds do not connect to plain SOCKS profiles because '
          'the device-to-endpoint path is not encrypted.';
    }

    if (lower.startsWith('vless://')) {
      final uri = Uri.tryParse(value);
      final security =
          uri?.queryParameters['security']?.trim().toLowerCase() ?? '';
      if (security != 'tls' && security != 'reality') {
        return 'Google Play builds require VLESS profiles to use TLS or Reality.';
      }
    }

    if (lower.startsWith('trojan://') ||
        lower.startsWith('vmess://') ||
        lower.startsWith('ss://') ||
        lower.startsWith('hysteria2://') ||
        lower.startsWith('hy2://') ||
        lower.startsWith('vless://')) {
      return null;
    }

    return 'This profile type is not approved for the Google Play build.';
  }
}
