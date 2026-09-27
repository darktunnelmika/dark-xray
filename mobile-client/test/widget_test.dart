import 'package:dark_xray_client/main.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('renders DarkXray cyber home', (tester) async {
    await tester.pumpWidget(const DarkXrayApp());

    expect(find.text('READY'), findsOneWidget);
    expect(find.text('DARKXRAY NETWORK'), findsOneWidget);
    expect(find.text('RED WRAITH · TUNNEL'), findsOneWidget);
  });
}
