# DarkXray Google Play release checklist

## Technical
- [x] Final application ID: `com.darkxray.client`
- [x] Version name: `1.0.0`
- [x] Version code: `100`
- [x] Compile SDK: 36
- [x] Target SDK: 36
- [x] Android VpnService declared
- [x] Always-on VPN support declared
- [x] Cleartext app traffic disabled
- [x] HTTP subscription URLs blocked
- [x] Plain SOCKS blocked in Play build
- [x] VLESS without TLS/Reality blocked in Play build
- [x] First-connect VpnService disclosure and affirmative consent
- [x] Privacy policy available in the app
- [x] 64-bit ARM support
- [x] Existing ARM64 release native libraries checked for >=16 KB ELF LOAD alignment
- [ ] Upload-key-signed AAB generated and verified
- [ ] Test final signed AAB through Play Internal Testing

## Play Console
- [ ] Create app: DarkXray
- [ ] App category: Tools
- [ ] Add privacy policy URL
- [ ] Complete Data Safety form
- [ ] Complete VpnService declaration
- [ ] Complete Content Rating
- [ ] Complete Target Audience
- [ ] Declare Ads: No
- [ ] App access: no restricted login required
- [ ] Add support contact
- [ ] Upload 512×512 app icon
- [ ] Upload 1024×500 feature graphic
- [ ] Upload accurate phone screenshots from the final build
- [ ] Upload signed AAB to Internal Testing first
- [ ] Run Pre-launch report
- [ ] Fix any Android vitals / policy warnings
- [ ] Move to Closed Testing if account requires it
- [ ] For qualifying new personal accounts: keep at least 12 testers opted in continuously for 14 days before applying for production access

## Privacy hosting
- [x] Static HTML policy committed under `docs/privacy/index.html`
- [x] GitHub Pages deployment workflow prepared
- [ ] Enable GitHub Pages for this repository once, with source set to **GitHub Actions**
- [ ] Confirm public URL loads without login or JavaScript:
  `https://darktunnelmika.github.io/dark-xray/privacy/`

## Before production
- [ ] Confirm whether developer-operated VPN/subscription servers store logs
- [ ] Make Data Safety and Privacy Policy match actual server behavior
- [ ] Store upload keystore and passwords in at least two secure offline locations
- [ ] Enable Play App Signing
