import '../models/proxy_profile.dart';
import 'subscription_service.dart';

class ImportResolver {
  const ImportResolver();

  static const _service = SubscriptionService();

  Future<List<ProxyProfile>> resolve(String input) async {
    final value = input.trim();
    if (value.isEmpty) {
      throw const SubscriptionException('Nothing to import.');
    }

    final uri = Uri.tryParse(value);
    if (uri != null &&
        (uri.scheme == 'http' || uri.scheme == 'https') &&
        uri.hasAuthority) {
      return _service.fetchAndParse(value);
    }

    return _service.parsePayload(value);
  }
}
