import 'package:shared_preferences/shared_preferences.dart';

class PlayComplianceStore {
  static const _vpnDisclosureAccepted =
      'darkxray.play.vpnDisclosureAccepted.v1';

  Future<bool> hasAcceptedVpnDisclosure() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_vpnDisclosureAccepted) ?? false;
  }

  Future<void> acceptVpnDisclosure() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_vpnDisclosureAccepted, true);
  }

  Future<void> resetVpnDisclosure() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_vpnDisclosureAccepted);
  }
}
