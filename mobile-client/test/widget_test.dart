import 'package:dark_xray_client/main.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('renders DARK XRAY home', (tester) async {
    await tester.pumpWidget(const DarkXrayApp());

    expect(find.text('DARK XRAY'), findsOneWidget);
    expect(find.text('DISCONNECTED'), findsOneWidget);
    expect(find.text('Subscription'), findsOneWidget);
  });
}
