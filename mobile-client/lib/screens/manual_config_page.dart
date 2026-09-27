import 'package:flutter/material.dart';

import '../models/proxy_profile.dart';
import '../services/import_resolver.dart';
import '../services/subscription_service.dart';

class ManualConfigPage extends StatefulWidget {
  const ManualConfigPage({super.key});

  @override
  State<ManualConfigPage> createState() => _ManualConfigPageState();
}

class _ManualConfigPageState extends State<ManualConfigPage> {
  final _controller = TextEditingController();
  final _resolver = const ImportResolver();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final raw = _controller.text.trim();
    if (raw.isEmpty) {
      setState(() => _error = 'Paste a proxy share link first.');
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      final profiles = await _resolver.resolve(raw);
      if (!mounted) return;
      Navigator.of(context).pop<List<ProxyProfile>>(profiles);
    } on SubscriptionException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } catch (_) {
      if (mounted) setState(() => _error = 'This configuration could not be parsed.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    const red = Color(0xFFFF1744);
    return Scaffold(
      backgroundColor: const Color(0xFF030406),
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        foregroundColor: red,
        title: const Text(
          'MANUAL CONFIGURATION',
          style: TextStyle(
            color: Colors.white,
            fontSize: 14,
            fontWeight: FontWeight.w800,
            letterSpacing: 1.3,
          ),
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.all(18),
        children: [
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: const Color(0xFF090C11),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: const Color(0x66FF1744)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'SHARE LINK / URI',
                  style: TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.w800,
                    letterSpacing: 0.8,
                  ),
                ),
                const SizedBox(height: 8),
                const Text(
                  'Supports VLESS, VMess, Trojan, Shadowsocks, Hysteria2 and SOCKS.',
                  style: TextStyle(
                    color: Color(0xFF8B96A5),
                    fontSize: 11,
                    height: 1.4,
                  ),
                ),
                const SizedBox(height: 14),
                TextField(
                  controller: _controller,
                  minLines: 4,
                  maxLines: 9,
                  autocorrect: false,
                  enableSuggestions: false,
                  style: const TextStyle(color: Colors.white, fontSize: 12),
                  decoration: InputDecoration(
                    hintText: 'vless://...\nvmess://...\ntrojan://...',
                    hintStyle: const TextStyle(color: Color(0xFF667080)),
                    filled: true,
                    fillColor: const Color(0xFF05070A),
                    enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: const BorderSide(color: Color(0xFF202834)),
                    ),
                    focusedBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: const BorderSide(color: red),
                    ),
                  ),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 10),
                  Text(
                    _error!,
                    style: const TextStyle(color: red, fontSize: 12),
                  ),
                ],
                const SizedBox(height: 14),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: _busy ? null : _save,
                    style: FilledButton.styleFrom(
                      backgroundColor: red,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                    ),
                    icon: _busy
                        ? const SizedBox(
                            width: 17,
                            height: 17,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white,
                            ),
                          )
                        : const Icon(Icons.check_rounded),
                    label: Text(_busy ? 'VALIDATING' : 'ADD CONFIGURATION'),
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
