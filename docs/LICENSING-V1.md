# DARK XRAY Licensing V1

V1 separates the customer HUB from a dedicated license authority. The authority keeps the Ed25519 private signing key; HUBs receive only its public key. A stable HTTPS authority domain is required and plain HTTP is rejected.

Each HUB creates a durable 128-bit installation identity under `/var/lib/dark-xray/license/`. Activation binds one license key to that identity and records the Telegram user ID in the signed lease. Successful checks issue a lease for at most five days. The HUB checks hourly for a due refresh; after a five-day lease there is a two-day offline grace. Revoked, expired, or offline-expired enforced licenses make panel mutations read-only. V1 intentionally does not kill existing proxy traffic.

Enforcement is opt-in. An upgraded existing installation remains `unconfigured` and writable until the Owner configures an HTTPS authority, pins its Ed25519 public key, activates a license, and explicitly enables enforcement. This prevents rollout from locking existing Production.

For migrations, the authority `replace` operation revokes the old key and creates a new unbound key for the same Telegram owner and remaining expiry. Historical records are retained for audit; the old key cannot activate another installation.

The authority admin API is the integration boundary for a central Telegram sales bot: create requires `telegram_id`, and replace/revoke operate on the license ID. The admin token and signing private key must never be copied to customer HUBs or committed to Git. Payment automation is deliberately outside Licensing V1 and can call this admin API only after a payment is finalized.