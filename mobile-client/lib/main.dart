import 'package:flutter/material.dart';

import 'screens/home_page.dart';
import 'ui/cyber.dart';

void main() {
  runApp(const DarkXrayApp());
}

class DarkXrayApp extends StatelessWidget {
  const DarkXrayApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'DarkXray',
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: CyberPalette.bg,
        colorScheme: const ColorScheme.dark(
          primary: CyberPalette.red,
          secondary: CyberPalette.cyan,
          surface: CyberPalette.panel,
        ),
        useMaterial3: true,
      ),
      home: const HomePage(),
    );
  }
}
