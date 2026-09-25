# DARK XRAY — Telegram Commerce V1

## Scope

Telegram is a first-class client of the existing DARK XRAY core. It does not
own a second customer database, a second credit ledger, or a second Xray model.

Each Owner/Representative panel can configure one Telegram bot with Bot Token,
numeric Telegram Admin ID, and enabled/disabled state. The same bot exposes
customer sales functions to normal Telegram users and scoped management
functions only to the configured numeric Admin ID.

## Trust boundaries

- Bot Token is encrypted with the existing DARK Fernet key.
- The full token is never returned by panel APIs.
- Telegram webhook requests use a random public path plus the
  X-Telegram-Bot-Api-Secret-Token header.
- Telegram update_id is deduplicated before side effects.
- Admin access is checked on the backend with the numeric Telegram user ID.
  Hiding buttons is not treated as authorization.
- Representative management continues to use the existing Actor scope.
- Reset traffic requires a second explicit Telegram command.
- The primary Owner bot can create a Representative; Representative bots
  cannot create sub-representatives.

## Accounting model

Commerce and representative capacity are intentionally separate:

- shop_plans.price_amount plus currency = customer-facing sale price.
- shop_orders = immutable purchase snapshot.
- shop_payment_events = idempotent payment confirmation events.
- owners.volume_credit_bytes = limited-service allocation capacity.
- owners.unlimited_credit = unlimited-service allocation capacity.

Observed Xray traffic remains analytics and does not spend representative
resource credit.

A paid order is provisioned through the existing Manager.create path.
Therefore the normal inbound scope, representative limits, Volume Credit,
Unlimited Credit, Xray application state and audit behavior still apply.

## Purchase flow

1. Customer opens Store.
2. Customer selects a product/plan.
3. Bot displays the current plan snapshot and available ready payment methods.
4. Customer explicitly selects a payment method.
5. DARK creates a pending_payment order with a price/configuration snapshot.
6. Payment is verified or manually confirmed with an idempotent event ID.
7. DARK provisions the service through the existing Manager.
8. Success becomes fulfilled; the Subscription URL is delivered by the
   connected Telegram bot.
9. If provisioning fails after payment, for example insufficient
   representative credit, the order becomes fulfillment_failed, keeps the
   payment state, and can be safely retried.

## Payment adapters

Manual payment is operational in V1.

Gateway payment records can be created for configuration/UI purposes, but they
are deliberately not ready until a provider-specific adapter performs real
request/verify/callback validation. A gateway placeholder cannot create a
successful paid order.

Provider adapters must implement real payment verification and map a unique
provider transaction/reference to one DARK payment event. Never trust only a
browser redirect or customer-provided success flag.

## Telegram management V1

Numeric Admin ID can:

- View scoped users.
- Inspect a user and Subscription URL.
- Enable/disable a scoped user.
- Request and confirm traffic reset.
- View order attention count.
- On the primary Owner bot: list and create Representatives.

Representative bot commands are always restricted to the Representative's
existing DARK scope.

## Panel workspace

Telegram Bot is available in both Owner and Representative navigation.

The workspace includes real Telegram connection state, Bot Token/Admin ID
activation, products and variants, limited/unlimited plans, duration, quota,
inbound targets, IP/HWID limits, integer price and currency, payment methods,
orders, manual payment confirmation and retryable provisioning failures.

## Next adapters and surfaces

The V1 contracts are intended to be reused by provider-specific payment
gateways, Telegram Mini App, receipt review, renew/add-volume/upgrade checkout,
coupons, notifications/broadcast and Owner-wide representative commerce view.
