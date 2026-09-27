import 'package:dark_xray_client/main.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('renders functional DarkXray home', (tester) async {
    SharedPreferences.setMockInitialValues({});

    await tester.pumpWidget(const DarkXrayApp());
    await tester.pumpAndSettle();

    expect(find.text('DarkXray'), findsOneWidget);
    expect(find.text('READY'), findsOneWidget);
    expect(find.text('DARKXRAY NETWORK'), findsOneWidget);
    expect(find.text('NO CONFIGURATIONS'), findsOneWidget);
    expect(find.text('CLIPBOARD'), findsOneWidget);
    expect(find.text('QR SCAN'), findsOneWidget);
  });
}
