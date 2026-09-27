import 'package:dark_xray_client/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('renders DarkXray app shell', (tester) async {
    SharedPreferences.setMockInitialValues({});

    await tester.pumpWidget(const DarkXrayApp());
    await tester.pump(const Duration(seconds: 2));

    expect(find.text('DarkXray'), findsOneWidget);
    expect(find.byType(DarkXrayApp), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
  });
}
