# Razorpay checkout configuration (Phase 1)

## What is implemented

The app can create a server-side Razorpay order when the owner explicitly confirms a written scope and amount on a consented client delivery request. The browser receives only the Razorpay public key, order ID, amount and currency. The server-side key secret is never included in the page.

Each order is stored with the client-request reference, expected amount/currency and an expiring checkout token (only the token hash is stored). The client request is set to `AWAITING_PAYMENT` after the quote email is accepted by Brevo. The customer checkout callback verifies its signature but does **not** mark the request paid. Only the signed `payment.captured` webhook can set the request to `PAID`, and only if the order ID, amount, currency and request mapping match exactly. Delivery remains blocked until the owner confirms the written scope, payment is verified, and the owner explicitly approves delivery.

This flow currently supports INR only. The HTML is a private draft/blueprint, not a website automatically deployed to the customer's domain.

## Replit deployment secrets

Configure these in the **published deployment's secrets**, not only the development workspace:

- `RAZORPAY_KEY_ID` — use a Test Mode key ID initially.
- `RAZORPAY_KEY_SECRET` — matching Test Mode secret; server-only.
- `RAZORPAY_WEBHOOK_SECRET` — a separate secret you choose when creating the Razorpay webhook. This is not the API key secret.
- `PUBLIC_APP_URL` — exact HTTPS published app URL.
- `FLASK_SECRET_KEY` — existing strong application secret.
- `BREVO_API_KEY` and `BREVO_SENDER` — required to email a quote/payment link.
- `OWNER_NOTIFICATION_EMAIL` — owner notifications.
- `HF_TOKEN` (or legacy `HF_API_KEY`) — optional; template fallback remains available if not configured.
- `BREVO_WEBHOOK_TOKEN` — separate token for the Brevo delivery-events webhook. Do not reuse the Razorpay webhook secret.

Never commit or paste any secret into GitHub code, a customer form, or chat.

## Configure the Razorpay webhook

In Razorpay Dashboard **Test Mode**:

1. Open Account & Settings → Webhooks.
2. Add webhook URL: `https://revenue-engine--noreplysanchita.replit.app/webhooks/razorpay`.
3. Enter a new webhook secret and save the same value as `RAZORPAY_WEBHOOK_SECRET` in the published Replit deployment.
4. Select the `payment.captured` event.
5. Save the webhook and send a controlled Test Mode event if the dashboard supports it. Do not use live keys or a real payment in this phase.

The existing route verifies the webhook's signature over the original request body. A bad signature is rejected. A captured event is accepted for this flow only when the order has already been recorded, the stored quote amount/currency match, and the order is mapped to the correct client request.

## Test process

1. Configure Test Mode key ID, key secret and webhook secret in the **published deployment**.
2. Publish the tested code, reviewing the generated database migration before approval. Only approve the new additive `payments` columns/indexes after verifying the migration does not drop existing constraints or delete records.
3. Use a controlled test client request with consent and a private draft.
4. In the owner dashboard, confirm the written scope, enter the agreed INR amount, and click **Create order & email payment link**. This is the deliberate action that creates a Test Mode order and sends the payment link.
5. Complete a Razorpay Test Mode checkout using Razorpay's current test-payment instructions. Do not use a live payment method.
6. Verify the owner dashboard changes the request from `AWAITING_PAYMENT` to `PAID` only after the signed `payment.captured` webhook is processed and matches the same order/amount/currency.
7. Verify that duplicate webhook deliveries do not create another payment or repeat state changes, while mismatched amount/currency, bad signatures and payer mismatch remain blocked.
8. Verify delivery is still blocked until the owner confirms scope and explicitly approves sending the private draft.

If order creation or email returns an ambiguous timeout, do not automatically retry. Check provider activity and reconcile first.

## Later live rollout

Only after the Test Mode end-to-end checks pass: switch the API key ID/secret to Live Mode keys, create a separate Live Mode Razorpay webhook with its own configured secret, update the deployment secrets, and perform a deliberate owner-reviewed rollout. Live payments, customer delivery and automated acquisition must not be enabled just because the unit tests pass.
