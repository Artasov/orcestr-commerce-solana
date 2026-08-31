# @orcestr/commerce-solana-core

Framework-independent contracts and HTTP client for Orcestr Commerce Solana. Version 0.1 supports native SOL and Token-2022 through transaction requests only; static transfer requests and the legacy Token Program are rejected.

## Install

```bash
npm install @orcestr/commerce-solana-core
```

## API client

Pass the existing authenticated fetch from the host application. The package does not create or refresh authentication sessions.

```ts
import {
  SolanaCommerceClient,
  createAuthenticatedFetchExecutor,
  selectSolanaCommerceOptions,
} from "@orcestr/commerce-solana-core";

const client = new SolanaCommerceClient({
  baseUrl: "https://app.example.com/api/v1/",
  executor: createAuthenticatedFetchExecutor(authFetch),
});

const response = await client.listCommercePaymentOptions(orderPublicId);
const option = selectSolanaCommerceOptions(response)[0];
if (!option) throw new Error("No Solana payment option");

const payment = await client.createCommercePaymentAttempt({
  orderPublicId,
  paymentOptionId: option.id,
  idempotencyKey,
});
const intent = await client.getPaymentIntent(payment.id);
```

This is the stable CommerceXL 0.3.2 flow: order → provider-neutral payment options → payment attempt. Every option contains the mandatory exact order `amount` and normalized `currency`, so checkout can display the server-owned snapshot without reading a mutable product row or trusting client pricing. `CommercePayment` is the canonical order-card status; the Solana intent adds the immutable settlement snapshot needed by checkout. Reopen a payment with `issueCommerceCheckoutAction(payment.id)` or `issuePaymentIntentAction(payment.id)`; both issue a fresh short-lived capability without replacing the payment/reference.

For Orcestr Auth, `authFetch` must retain the host's cookie/OAuth, refresh, CSRF, and typed API error behavior. Capability-bearing responses are marked with `responseSensitivity: "capability"` for custom executors. The built-in executor redacts their HTTP error body, and parser errors never retain raw rejected values. Custom executors must likewise avoid logging or persisting capability bodies and URLs.

The default CommerceXL routes are:

- `GET /commerce/orders/{order_public_id}/payment-options/`
- `POST /commerce/orders/{order_public_id}/payment-attempts/`
- `GET /commerce/payments/{payment_public_id}/`
- `POST /commerce/payments/{payment_public_id}/checkout-action/`

The Solana detail routes are:

- `GET /commerce/solana/orders/{order_public_id}/payment-options`
- `POST /commerce/solana/payment-intents`
- `GET /commerce/solana/payment-intents/{payment_public_id}`
- `POST /commerce/solana/payment-intents/{payment_public_id}/candidate-signatures`
- `POST /commerce/solana/payment-intents/{payment_public_id}/cancel`
- `POST /commerce/solana/payment-intents/{payment_public_id}/actions`

Routes are replaceable through `SolanaCommerceRoutes` when a host mounts the router under another prefix. A custom route must remain an absolute path under the configured API base path; traversal, protocol-relative, backslash-normalized, and control-character paths are rejected before the authenticated executor runs. The built-in authenticated and public fetch executors reject HTTP redirects.

## Public transaction request

Use a separate executor. It always sends `credentials: "omit"`, `Cache-Control: no-store` semantics, `Referrer-Policy: no-referrer`, and `redirect: "error"`; it never invokes auth refresh or CSRF logic.

```ts
import {
  createPublicTransactionRequestExecutor,
  createTransactionRequest,
} from "@orcestr/commerce-solana-core";

const payload = await createTransactionRequest(
  createPublicTransactionRequestExecutor(),
  intent.action.uri,
  payerAddress,
);
```

Keep the canonical URI only in memory. It contains a short-lived capability and must not enter analytics, logs, browser history, storage, or error reporting. Transaction-request endpoints must be HTTPS; `http://localhost`, `http://127.0.0.1`, and `http://[::1]` are accepted only for local development.

## Exact amounts and verification boundary

Commercial decimal amounts and blockchain base units remain strings. `parseDecimalAmount`, `parseRawAmount`, `decimalAmountToRaw`, and `rawAmountToDecimal` never round through JavaScript `Number`.

Quote snapshots require an explicit rounding mode. `exact` means the backend converted a database order amount directly to asset raw units and rejected unsupported fractional precision; a missing rounding field is never interpreted as a legacy default.

Runtime parsers bind the cluster to its exact genesis hash, decode addresses/signatures to 32/64 bytes, enforce positive u64 raw amounts and option bounds, and require `display_amount == expected_raw_amount / 10^decimals`. Every immutable settlement also carries a required canonical `recipient_policy_version` snapshot for resolver provenance; it is audit metadata and is not substituted for the exact recipient address during verification.

`validateInspectedTransaction` accepts only one exact top-level native transfer or Token-2022 `TransferChecked`, with the expected payer, canonical payer Token-2022 associated token account, mint, destination token account, raw amount, decimals, reference, required signed `orcestr-issuance:<uuid4>` memo, and exact optional settlement memo. The built-in inspector also requires the issuance memo first, the optional settlement memo second, and the payment instruction last. It is a client-side safety check; authoritative payment verification remains on the backend after the required Solana commitment.

## Build

For local integration, build the source workspace first, use the exact consumer dependency `file:../../orcestr-commerce-solana/frontend/packages/core`, and run the consumer's `npm install` before its dev server. Do not use a global `npm link`; restore version `0.2.0` from the registry before a release commit.

From the repository `frontend` directory:

```bash
npm run typecheck
npm test
npm run build
npm pack --dry-run --workspace @orcestr/commerce-solana-core
```
