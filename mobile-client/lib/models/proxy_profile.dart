import 'dart:convert';

class ProxyProfile {
  const ProxyProfile({
    required this.id,
    required this.name,
    required this.scheme,
    required this.detail,
    required this.rawUri,
    this.host,
    this.port,
    this.ping = 'n/a',
  });

  final String id;
  final String name;
  final String scheme;
  final String detail;
  final String rawUri;
  final String? host;
  final int? port;
  final String ping;

  bool get isDirect => name.toUpperCase().contains('DIRECT');

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'scheme': scheme,
        'detail': detail,
        'rawUri': rawUri,
        'host': host,
        'port': port,
        'ping': ping,
      };

  factory ProxyProfile.fromJson(Map<String, dynamic> json) {
    return ProxyProfile(
      id: json['id'] as String? ?? '',
      name: json['name'] as String? ?? 'Unnamed',
      scheme: json['scheme'] as String? ?? 'unknown',
      detail: json['detail'] as String? ?? '',
      rawUri: json['rawUri'] as String? ?? '',
      host: json['host'] as String?,
      port: (json['port'] as num?)?.toInt(),
      ping: json['ping'] as String? ?? 'n/a',
    );
  }

  String encode() => jsonEncode(toJson());

  factory ProxyProfile.decode(String source) =>
      ProxyProfile.fromJson(jsonDecode(source) as Map<String, dynamic>);
}
