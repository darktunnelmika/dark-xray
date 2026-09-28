# DarkXray — Google Play review video script

Record a real phone screen. Keep the video under 90 seconds.

## Shot plan

### 0–8s — Launch
- Open DarkXray.
- Show the DarkXray home screen.
- Caption: **DarkXray is a VPN client. VPN is the core functionality.**

### 8–20s — Import
- Tap **+**.
- Import a test VLESS Reality/TLS, Trojan, VMess, Shadowsocks, or Hysteria2 configuration.
- Return to Home and select the profile.

### 20–35s — Prominent VPN disclosure
- Tap the power button.
- Show the separate **VPN CONNECTION DISCLOSURE**.
- Scroll enough for the reviewer to see the disclosure text.
- Tap **I AGREE**.
- Show Android's system VPN permission prompt.
- Approve it.

### 35–52s — Active VpnService
- Show DarkXray changing to **CONNECTED**.
- Show session time/upload/download counters.
- Pull down Android notifications and show the ongoing **DarkXray • Connected** notification.

### 52–65s — Foreground/background behavior
- Return to the Android home screen while the VPN remains active.
- Open a browser or a simple IP-check page to demonstrate connectivity.
- Return to DarkXray.

### 65–78s — Settings / privacy
- Open **System Settings**.
- Show **Privacy Policy**, **System Kill Switch**, and **Xray Core Status**.

### 78–88s — User-controlled stop
- Disconnect using the DarkXray power button or the notification Disconnect action.
- Show the connection returning to **READY**.

## Suggested caption text
"DarkXray uses Android VpnService only to create the user-requested encrypted VPN tunnel. The user explicitly starts and stops the VPN. DarkXray does not inject ads or redirect advertising traffic."
