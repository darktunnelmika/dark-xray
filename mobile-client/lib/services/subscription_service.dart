import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/proxy_profile.dart';

class SubscriptionException implements Exception {
  const SubscriptionException(this.message);
  final String message;

  @override
  String toString() => message;
}

class SubscriptionService {
  const SubscriptionService();

  Future<List<ProxyProfile>> fetchAndParse(String sourceUrl) async {
    final uri = Uri.tryParse(sourceUrl.trim());
    if (uri == null || !uri.hasScheme || !uri.hasAuthority) {
      throw const SubscriptionException('Subscription URL is not valid.');
    }

    final response = await http
        .get(
          uri,
          headers: const {
            'User-Agent': 'DarkXray/0.2 Android',
            'Accept': 'text/plain,*/*',
          },
        )
        .timeout(const Duration(seconds: 18));

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw SubscriptionException(
        'Server returned HTTP ${response.statusCode}.',
      );
    }

    return parsePayload(utf8.decode(response.bodyBytes, allowMalformed: true));
  }

  List<ProxyProfile> parsePayload(String input) {
    var payload = input.trim();
    if (payload.isEmpty) {
      throw const SubscriptionException('Subscription is empty.');
    }

    if (!_containsSupportedUri(payload)) {
      final decoded = _tryDecodeBase64(payload);
      if (decoded != null && _containsSupportedUri(decoded)) {
        payload = decoded;
      }
    }

    final lines = payload
        .replaceAll('\r', '')
        .split('\n')
        .map((line) => line.trim())
        .where((line) => line.isNotEmpty && !line.startsWith('#'));

    final profiles = <ProxyProfile>[];
    final seen = <String>{};

    for (final line in lines) {
      final profile = _parseLine(line);
      if (profile != null && seen.add(profile.rawUri)) {
        profiles.add(profile);
      }
    }

    if (profiles.isEmpty) {
      throw const SubscriptionException(
        'No supported VLESS, VMess, Trojan or Shadowsocks profiles found.',
      );
    }

    return profiles;
  }

  bool _containsSupportedUri(String value) {
    final lower = value.toLowerCase();
    return lower.contains('vless://') ||
        lower.contains('vmess://') ||
        lower.contains('trojan://') ||
        lower.contains('ss://') ||
        lower.contains('hysteria2://') ||
        lower.contains('hy2://') ||
        lower.contains('socks://');
  }

  ProxyProfile? _parseLine(String line) {
    final lower = line.toLowerCase();
    if (lower.startsWith('vless://')) {
      return _parseStandardUri(line, 'VLESS');
    }
    if (lower.startsWith('trojan://')) {
      return _parseStandardUri(line, 'TROJAN');
    }
    if (lower.startsWith('ss://')) {
      return _parseShadowsocks(line);
    }
    if (lower.startsWith('vmess://')) {
      return _parseVmess(line);
    }
    if (lower.startsWith('hysteria2://') || lower.startsWith('hy2://')) {
      return _parseSimpleModernUri(line, 'HYSTERIA2', 'QUIC | TLS');
    }
    if (lower.startsWith('socks://')) {
      return _parseSimpleModernUri(line, 'SOCKS', 'TCP/UDP');
    }
    return null;
  }

  ProxyProfile? _parseStandardUri(String raw, String schemeLabel) {
    try {
      final uri = Uri.parse(raw);
      final transport = uri.queryParameters['type'] ??
          uri.queryParameters['network'] ??
          'TCP';
      final security = uri.queryParameters['security'] ??
          uri.queryParameters['tls'] ??
          'none';
      final title = _profileName(uri.fragment, uri.host, schemeLabel);
      return ProxyProfile(
        id: _stableId(raw),
        name: title,
        scheme: schemeLabel.toLowerCase(),
        detail:
            '$schemeLabel | ${transport.toUpperCase()} | ${_prettySecurity(security)}',
        rawUri: raw,
        host: uri.host.isEmpty ? null : uri.host,
        port: uri.hasPort ? uri.port : null,
      );
    } catch (_) {
      return null;
    }
  }


  ProxyProfile? _parseSimpleModernUri(
    String raw,
    String schemeLabel,
    String transport,
  ) {
    try {
      final uri = Uri.parse(raw);
      final title = _profileName(uri.fragment, uri.host, schemeLabel);
      return ProxyProfile(
        id: _stableId(raw),
        name: title,
        scheme: schemeLabel.toLowerCase(),
        detail: '$schemeLabel | $transport',
        rawUri: raw,
        host: uri.host.isEmpty ? null : uri.host,
        port: uri.hasPort ? uri.port : null,
      );
    } catch (_) {
      return ProxyProfile(
        id: _stableId(raw),
        name: schemeLabel,
        scheme: schemeLabel.toLowerCase(),
        detail: '$schemeLabel | $transport',
        rawUri: raw,
      );
    }
  }

  ProxyProfile? _parseShadowsocks(String raw) {
    try {
      final uri = Uri.parse(raw);
      final title = _profileName(uri.fragment, uri.host, 'SHADOWSOCKS');
      return ProxyProfile(
        id: _stableId(raw),
        name: title,
        scheme: 'ss',
        detail: 'SHADOWSOCKS | TCP/UDP',
        rawUri: raw,
        host: uri.host.isEmpty ? null : uri.host,
        port: uri.hasPort ? uri.port : null,
      );
    } catch (_) {
      return ProxyProfile(
        id: _stableId(raw),
        name: 'SHADOWSOCKS',
        scheme: 'ss',
        detail: 'SHADOWSOCKS',
        rawUri: raw,
      );
    }
  }

  ProxyProfile? _parseVmess(String raw) {
    try {
      final encoded = raw.substring('vmess://'.length).trim();
      final decoded = _decodeBase64(encoded);
      final data = jsonDecode(decoded) as Map<String, dynamic>;
      final host = data['add']?.toString();
      final port = int.tryParse(data['port']?.toString() ?? '');
      final network = data['net']?.toString().trim();
      final tls = data['tls']?.toString().trim();
      final title = _profileName(
        data['ps']?.toString() ?? '',
        host ?? '',
        'VMESS',
      );
      return ProxyProfile(
        id: _stableId(raw),
        name: title,
        scheme: 'vmess',
        detail:
            'VMESS | ${(network == null || network.isEmpty ? 'TCP' : network).toUpperCase()} | ${_prettySecurity(tls ?? 'none')}',
        rawUri: raw,
        host: host,
        port: port,
      );
    } catch (_) {
      return null;
    }
  }

  String _profileName(String fragment, String host, String fallback) {
    var name = fragment.trim();
    if (name.isNotEmpty) {
      try {
        name = Uri.decodeComponent(name);
      } catch (_) {}
    }
    if (name.isEmpty) name = host.trim();
    if (name.isEmpty) name = fallback;
    return name;
  }

  String _prettySecurity(String value) {
    final normalized = value.trim().toLowerCase();
    if (normalized == 'reality') return 'Reality';
    if (normalized == 'tls') return 'TLS';
    if (normalized == 'none' || normalized.isEmpty) return 'Plain';
    return value;
  }

  String? _tryDecodeBase64(String source) {
    try {
      return _decodeBase64(source.replaceAll(RegExp(r'\s+'), ''));
    } catch (_) {
      return null;
    }
  }

  String _decodeBase64(String source) {
    var normalized = source.replaceAll('-', '+').replaceAll('_', '/');
    final remainder = normalized.length % 4;
    if (remainder != 0) {
      normalized += '=' * (4 - remainder);
    }
    return utf8.decode(base64Decode(normalized), allowMalformed: true);
  }

  String _stableId(String value) {
    var hash = 0x811C9DC5;
    for (final unit in utf8.encode(value)) {
      hash ^= unit;
      hash = (hash * 0x01000193) & 0xFFFFFFFF;
    }
    return hash.toRadixString(16).padLeft(8, '0');
  }
}
