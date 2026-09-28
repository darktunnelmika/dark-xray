# DarkXray — Google Play Data Safety draft

This draft is based on the current **mobile client code**. Re-check it against production VPN/subscription server behavior before submitting to Google Play.

## Current app-side behavior

- No advertising SDK.
- No analytics SDK.
- No account creation or login.
- No DarkXray telemetry backend in the mobile client.
- VPN/proxy profiles and settings are stored locally in app-private storage.
- Subscription requests are sent directly to the HTTPS URL supplied by the user.
- VPN traffic is sent to the VPN/proxy endpoint selected by the user.
- Local connection diagnostics include latency, connection status, session duration and app-level traffic counters.
- Camera access is used only when the user opens the QR scanner.
- The app does not request location permission.

## Suggested Data Safety answers — only if production backends do not collect user data

### Does your app collect or share any of the required user data types?
**No**, if:
1. DarkXray itself has no analytics/telemetry backend; and
2. developer-operated VPN/subscription endpoints do not retain data that Google considers collected on behalf of the app/developer.

Google notes that data used only on-device is not treated as collected, and some off-device ephemeral processing can be excluded depending on the exact circumstances.

### Is all user data encrypted in transit?
For Google Play connections, DarkXray requires HTTPS subscription URLs and an encrypted VPN/proxy path to the endpoint.

### Can users request deletion?
There is no DarkXray account. Local data can be deleted with **Settings → Reset Local Data**, Android **Clear storage**, or by uninstalling the app.

## STOP — verify server-side logging before selecting “No data collected”

If any developer-operated VPN, subscription, panel, API, crash-reporting, update, push, ad, analytics, or support backend stores or receives any of the following, the form must be updated:

- IP address
- Device or other identifiers
- Account information
- Subscription identifiers tied to a person
- VPN connection metadata
- DNS queries
- Browsing destinations or traffic metadata
- Crash logs or diagnostics sent off-device
- Support messages
- Purchase/payment information

Do not submit the “No data collected” answer until these backend practices are confirmed.
