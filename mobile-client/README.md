# DARK XRAY Client

Bootstrap for the dedicated DARK XRAY Android/iOS client.

## Current milestone — v0.1 bootstrap

- Independent Flutter UI layer
- Cyber Classic visual language
- Home / connection state prototype
- Server selector prototype
- Cloud Android build workflow
- No copied Hiddify/v2rayNG application code

## Architecture direction

The UI and application logic are developed independently. Open-source clients are used as architectural references only. The VPN integration will be implemented as a dedicated Android `VpnService` bridge to Xray core, followed later by an iOS Network Extension implementation.

## Cloud build

The GitHub Actions workflow creates a temporary Flutter Android host project, injects the DARK XRAY Dart sources, runs analysis/tests, and builds a debug APK. This keeps local Android Studio out of the development loop.

## Next milestone

1. Subscription URL import
2. URI/config parsing
3. Persistent profile store
4. Real Android VPN bridge
5. Xray core lifecycle (start/stop/status)
