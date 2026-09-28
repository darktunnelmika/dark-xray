# DarkXray — Play Console submission answers

These answers are prepared for **DarkXray 1.0.0 / com.darkxray.client**.

## Create app
- App name: **DarkXray**
- Default language: **English (United States)**
- App or game: **App**
- Free or paid: **Free**
- Category: **Tools**

## Store listing
Use:
- `mobile-client/play/listings/en-US/title.txt`
- `mobile-client/play/listings/en-US/short_description.txt`
- `mobile-client/play/listings/en-US/full_description.txt`

Persian localization is under:
- `mobile-client/play/listings/fa-IR/`

## Privacy policy
Planned public URL:
- https://darktunnelmika.github.io/dark-xray/privacy/

The same privacy policy is available inside the app under **System Settings → Privacy Policy**.

## App access
- Does the app require restricted login credentials for review? **No**
- Reviewers can import a normal compatible test profile or subscription and use the VPN without account login.

If the production build later requires a private subscription, provide Google review credentials/test access here.

## Ads
- Does your app contain ads? **No**

## Target audience
Suggested initial selection:
- **18 and over**

Reason: DarkXray is a general-purpose network/VPN utility and is not designed specifically for children.

## News apps
- Is this a news app? **No**

## Government apps
- Is this an official government app? **No**

## Financial features
- No financial services are provided by the mobile VPN client.

## Health
- No health functionality.

## Content rating
DarkXray is a network utility. The app itself contains:
- no violence
- no sexual content
- no profanity
- no gambling
- no controlled substances
- no user-generated social feed

Answer the IARC questionnaire according to the final build and store listing.

## Data Safety
Use `mobile-client/play/data-safety.md`.

Do not submit the final Data Safety form until VPN/subscription server logging is confirmed.

## VpnService declaration
Use `mobile-client/play/vpnservice-declaration.md`.

Core question:
- **Is providing a VPN the core functionality? → Yes**

A short review video is required by Google's VpnService declaration. See:
- `mobile-client/play/review-video-script.md`

## Foreground service declaration
DarkXray uses:
- `FOREGROUND_SERVICE`
- `FOREGROUND_SERVICE_SYSTEM_EXEMPTED`
- foreground service type: `systemExempted`

Functionality:
- User starts a VPN connection.
- DarkXray keeps the active Android VpnService tunnel running while the user expects VPN connectivity.
- A persistent notification shows that the VPN is active and includes a Disconnect action.

Impact if deferred:
- The user-requested VPN tunnel would not start immediately, leaving the device without the requested VPN connection.

Impact if interrupted:
- Network traffic would stop using the requested VPN tunnel until the service reconnects or the user restarts it.

Review video:
- The same VPN review video can demonstrate the foreground service: connect, show Android VPN/notification state, background the app, return, disconnect.

## Permissions visible to Google Play
- INTERNET — VPN/subscription functionality
- ACCESS_NETWORK_STATE — auto-reconnect across network changes
- CAMERA — QR scanner, only when opened by the user
- FOREGROUND_SERVICE — active VPN tunnel
- FOREGROUND_SERVICE_SYSTEM_EXEMPTED — Android VPN foreground service
- POST_NOTIFICATIONS — ongoing VPN status notification on supported Android versions

DarkXray does not request location permission.

## Package / release
- Application ID: **com.darkxray.client**
- Version name: **1.0.0**
- Version code: **100**
- Compile SDK: **36**
- Target SDK: **36**
- Distribution format: **Android App Bundle (.aab)**
- Recommended first track: **Internal testing**

## Production access for qualifying new personal developer accounts
If the Play developer account is a personal account created after November 13, 2023, Google currently requires a closed test with at least **12 testers continuously opted in for 14 days** before applying for production access.
