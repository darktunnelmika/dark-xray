import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/proxy_profile.dart';
import '../services/profile_store.dart';
import '../services/subscription_service.dart';

class SubscriptionsPage extends StatefulWidget {
  const SubscriptionsPage({super.key});

  @override
  State<SubscriptionsPage> createState() => _SubscriptionsPageState();
}

class _SubscriptionsPageState extends State<SubscriptionsPage> {
  static const _red = Color(0xFFFF1744);
  static const _bg = Color(0xFF030406);
  static const _panel = Color(0xFF0D1118);
  static const _muted = Color(0xFF8B96A5);

  final _controller = TextEditingController();
  final _service = const SubscriptionService();
  final _store = ProfileStore();
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadExistingSource();
  }

  Future<void> _loadExistingSource() async {
    final source = await _store.loadSourceUrl();
    if (!mounted || source == null) return;
    _controller.text = source;
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final value = data?.text?.trim();
    if (value == null || value.isEmpty) return;
    setState(() {
      _controller.text = value;
      _controller.selection = TextSelection.collapsed(
        offset: _controller.text.length,
      );
      _error = null;
    });
  }

  Future<void> _import() async {
    final url = _controller.text.trim();
    if (url.isEmpty) {
      setState(() => _error = 'Paste a subscription URL first.');
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final profiles = await _service.fetchAndParse(url);
      await _store.saveProfiles(profiles, sourceUrl: url);
      if (!mounted) return;
      Navigator.of(context).pop<List<ProxyProfile>>(profiles);
    } on SubscriptionException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } catch (_) {
      if (mounted) {
        setState(() => _error = 'Could not download this subscription.');
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        foregroundColor: _red,
        title: const Text(
          'SUBSCRIPTIONS',
          style: TextStyle(
            color: Colors.white,
            fontWeight: FontWeight.w800,
            fontSize: 15,
            letterSpacing: 1.8,
          ),
        ),
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(18, 12, 18, 24),
          children: [
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: _panel,
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: _red.withValues(alpha: 0.55)),
                boxShadow: [
                  BoxShadow(
                    color: _red.withValues(alpha: 0.08),
                    blurRadius: 22,
                  ),
                ],
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Row(
                    children: [
                      Icon(Icons.link_rounded, color: _red),
                      SizedBox(width: 10),
                      Text(
                        'IMPORT SUBSCRIPTION',
                        style: TextStyle(
                          color: Colors.white,
                          fontWeight: FontWeight.w800,
                          letterSpacing: 0.8,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  TextField(
                    controller: _controller,
                    keyboardType: TextInputType.url,
                    autocorrect: false,
                    enableSuggestions: false,
                    minLines: 2,
                    maxLines: 4,
                    style: const TextStyle(color: Colors.white, fontSize: 13),
                    decoration: InputDecoration(
                      hintText: 'https://example.com/sub/…',
                      hintStyle: const TextStyle(color: _muted),
                      filled: true,
                      fillColor: const Color(0xFF070A0F),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10),
                        borderSide: const BorderSide(color: Color(0xFF202834)),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10),
                        borderSide: const BorderSide(color: _red),
                      ),
                    ),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 10),
                    Text(
                      _error!,
                      style: const TextStyle(color: _red, fontSize: 12),
                    ),
                  ],
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: _loading ? null : _paste,
                          icon: const Icon(Icons.content_paste_rounded),
                          label: const Text('PASTE'),
                          style: OutlinedButton.styleFrom(
                            foregroundColor: _red,
                            side: const BorderSide(color: _red),
                            padding: const EdgeInsets.symmetric(vertical: 13),
                          ),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: FilledButton.icon(
                          onPressed: _loading ? null : _import,
                          icon: _loading
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    color: Colors.white,
                                  ),
                                )
                              : const Icon(Icons.download_rounded),
                          label: Text(_loading ? 'SYNCING' : 'IMPORT'),
                          style: FilledButton.styleFrom(
                            backgroundColor: _red,
                            foregroundColor: Colors.white,
                            padding: const EdgeInsets.symmetric(vertical: 13),
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 18),
            const _InfoTile(
              icon: Icons.shield_outlined,
              title: 'Supported formats',
              body: 'VLESS • VMess • Trojan • Shadowsocks',
            ),
            const _InfoTile(
              icon: Icons.save_outlined,
              title: 'Stored on this device',
              body: 'Imported profiles stay available after restarting DarkXray.',
            ),
            const _InfoTile(
              icon: Icons.sync_rounded,
              title: 'Safe refresh',
              body: 'Importing the same subscription replaces the local profile list.',
            ),
          ],
        ),
      ),
    );
  }
}

class _InfoTile extends StatelessWidget {
  const _InfoTile({
    required this.icon,
    required this.title,
    required this.body,
  });

  final IconData icon;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: const Color(0xFF090C11),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: const Color(0xFF202834)),
        ),
        child: Row(
          children: [
            Icon(icon, color: const Color(0xFFFF1744), size: 21),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: const TextStyle(
                      color: Colors.white,
                      fontWeight: FontWeight.w700,
                      fontSize: 13,
                    ),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    body,
                    style: const TextStyle(
                      color: Color(0xFF8B96A5),
                      fontSize: 11,
                      height: 1.35,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
