# DarkXray — Google Play VpnService declaration draft

## Is providing a VPN the core functionality of the application?
**Yes.**

## Why does the app use VpnService?
DarkXray is a VPN client. Android VpnService is used to create the device-level TUN interface after the user explicitly starts a connection. The TUN traffic is passed to the bundled XTLS Xray core and sent to the VPN/proxy endpoint selected by the user.

## What traffic is handled?
Network traffic from the device that Android routes into the VpnService tunnel. The app also handles DNS traffic configured for the VPN connection.

## Is VpnService used for monetization, ad injection, or ad traffic redirection?
**No.**

DarkXray does not inject advertisements into traffic, redirect advertising traffic, or sell browsing traffic.

## Does the app collect personal or sensitive data through VpnService?
The current mobile client does not send VPN browsing telemetry to a DarkXray analytics service. Traffic is processed as required to provide the VPN connection and is transmitted to the user-selected VPN/proxy endpoint.

**Important before submission:** confirm the logging/retention practices of every VPN endpoint operated by the developer. If developer-operated servers store IP addresses, traffic metadata, DNS queries, browsing destinations, or other user data, update the Privacy Policy, Data Safety form, and this declaration before submission.

## Encryption
The Play build blocks plain SOCKS profiles and VLESS profiles without TLS or Reality. Supported Play connections must provide an encrypted path from the device to the VPN/proxy endpoint.

## Prominent disclosure
Before the first VPN connection, DarkXray displays a separate in-app VpnService disclosure and requires an affirmative **I AGREE** action. The app then requests Android's VPN permission.

## Store listing
The full store description explicitly states that VPN is the core functionality and explains use of VpnService.
