import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/proxy_profile.dart';
import '../services/import_resolver.dart';
import '../services/subscription_service.dart';
import '../ui/cyber.dart';
import 'manual_config_page.dart';
import 'qr_scanner_page.dart';

class AddConfigurationPage extends StatefulWidget {
  const AddConfigurationPage({super.key});

  @override
  State<AddConfigurationPage> createState() => _AddConfigurationPageState();
}

class _AddConfigurationPageState extends State<AddConfigurationPage> {
  final _resolver = const ImportResolver();
  bool _busy = false;

  Future<void> _resolveAndReturn(String raw) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final profiles = await _resolver.resolve(raw);
      if (!mounted) return;
      Navigator.of(context).pop<List<ProxyProfile>>(profiles);
    } on SubscriptionException catch (error) {
      _showError(error.message);
    } catch (error) {
      _showError('Import failed: $error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text?.trim();
    if (text == null || text.isEmpty) {
      _showError('Clipboard is empty.');
      return;
    }
    await _resolveAndReturn(text);
  }

  Future<void> _scan() async {
    final raw = await Navigator.of(context).push<String>(
      MaterialPageRoute(builder: (_) => const QrScannerPage()),
    );
    if (!mounted || raw == null || raw.trim().isEmpty) return;
    await _resolveAndReturn(raw);
  }

  Future<void> _pickFile() async {
    try {
      final file = await FilePicker.pickFile();
      if (file == null) return;
      final bytes = await file.readAsBytes();
      if (bytes.isEmpty) {
        _showError('Selected file is empty.');
        return;
      }
      if (bytes.length > 16 * 1024 * 1024) {
        _showError('Configuration file is larger than 16 MiB.');
        return;
      }
      final text = utf8.decode(bytes, allowMalformed: true).trim();
      await _resolveAndReturn(text);
    } catch (error) {
      _showError('Could not read selected file: $error');
    }
  }

  Future<void> _manual() async {
    final profiles = await Navigator.of(context).push<List<ProxyProfile>>(
      MaterialPageRoute(builder: (_) => const ManualConfigPage()),
    );
    if (!mounted || profiles == null || profiles.isEmpty) return;
    Navigator.of(context).pop<List<ProxyProfile>>(profiles);
  }

  @override
  Widget build(BuildContext context) {
    return CyberPage(
      title: 'ADD CONFIGURATION',
      child: CyberFrame(
        child: Column(
          children: [
            if (_busy) ...[
              const LinearProgressIndicator(
                color: CyberPalette.red,
                backgroundColor: CyberPalette.panelSoft,
              ),
              const SizedBox(height: 12),
            ],
            CyberMenuTile(
              icon: Icons.content_paste_rounded,
              title: 'Paste Link',
              subtitle: 'Import a share link or subscription URL',
              onTap: _paste,
            ),
            CyberMenuTile(
              icon: Icons.qr_code_scanner_rounded,
              title: 'Scan QR Code',
              subtitle: 'Use the camera to import a QR code',
              onTap: _scan,
            ),
            CyberMenuTile(
              icon: Icons.insert_drive_file_outlined,
              title: 'Import File',
              subtitle: 'Open a subscription or configuration text file',
              onTap: _pickFile,
            ),
            CyberMenuTile(
              icon: Icons.tune_rounded,
              title: 'Manual Configuration',
              subtitle: 'Validate and add one or more share links manually',
              onTap: _manual,
            ),
          ],
        ),
      ),
    );
  }
}
