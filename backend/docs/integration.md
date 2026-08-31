# Host integration

## Database and CommerceXL

Use CommerceXL 0.3.2 or newer and import `orcestr_commerce_solana.models` before the host collects `CommerceBase.metadata`. Version 0.3.2 is the minimum supported baseline because payment options must carry the order amount/currency snapshot and `CONFIRMED -> EXPIRED` is required after a complete final reference scan. The addon deliberately ships no migrations: the host creates reviewed Alembic migrations for its own database topology.

Create and explicitly register `SolanaProviderRegistrationFactory.create(dependencies)`. Asset options must come from validated `ValidatedSolanaAsset` objects. An active Token-2022 asset cannot be constructed without its on-chain account-data hash.

`StaticRecipientResolver` is suitable for a treasury flow. P2P applications should implement `RecipientResolver` using a wallet binding already verified by the host; this package does not expose a partial SIWS implementation.

Every `RecipientSnapshot` must carry a stable canonical `policy_version` (1-100 ASCII characters from the documented code alphabet). It is copied to required `settlement.recipient_policy_version` in the immutable ORM JSON/API snapshot for treasury/P2P audit; it is provenance, not an on-chain identity field.

Every provider registration must inject `SettlementPriceKeyResolver.resolve(session, order, actor) -> str | None`. The host should return a stable plan, addon, or AI credit-pack code. Returning `None` hides Solana options for that order and rejects creation. There is deliberately no `order.kind` fallback because multiple commercial products can share one kind.

Use `OrderSnapshotSettlementQuoteProvider({option_id: commerce_currency}, version=...)` for normal database-priced orders. For ORCESTR, the mapping is `{"solana_orcestr": "ORCESTR"}` and the CommerceXL product must have a separate active `ORCESTR` price row. CommerceXL copies that price into the immutable order and publishes the same exact decimal amount and normalized currency on every `PaymentOptionDTO`. The provider accepts only the exact currency configured for the validated asset option, converts the order decimal using the asset decimals, and records explicit `rounding="exact"`; it never reads a current catalogue row after order creation and never rounds or uses float arithmetic. Asset identity comes from option id plus the validated mint policy, never from its display symbol.

`FixedSettlementQuoteProvider` remains available for deliberately precomputed raw settlement amounts and accepts `{(resolved_price_key, option_id): raw_amount}`. Do not use it to mirror editable catalogue prices in environment variables.

## Default services

Build the authenticated service with `SolanaApplicationService.build_default(config=..., rpc=..., clock=..., session_factory=..., payment_runtime=...)`. Put it into `SolanaFastApiConfig` together with host adapters for:

- current Orcestr Auth actor;
- order/payment ownership and permission checks;
- CSRF validation on authenticated mutations.

Mount the router returned by `SolanaFastApiRouterFactory.create(...)`. The default paths are:

- `GET /commerce/solana/orders/{order_public_id}/payment-options`
- `POST /commerce/solana/payment-intents`
- `GET /commerce/solana/payment-intents/{payment_public_id}`
- `POST /commerce/solana/payment-intents/{payment_public_id}/actions`
- `POST /commerce/solana/payment-intents/{payment_public_id}/candidate-signatures`
- `POST /commerce/solana/payment-intents/{payment_public_id}/cancel`
- public Solana Pay GET/POST `/commerce/solana/transaction-requests/{capability}`

Solana cancellation must enter through `SolanaApplicationService.cancel_intent()` (the package `/cancel` route). It locks and records the Solana cancellation command before invoking CommerceXL, preserving the global Solana-intent then Commerce order/payment lock order used by reconciliation. Calling generic `PaymentRuntime.cancel_for_order()` directly for a Solana payment is intentionally rejected by the provider before it mutates the Solana row; this fail-fast guard prevents the inverse lock order and a database deadlock.

JSON is snake_case. Raw amounts and rates are decimal strings. `action` is nullable after it is no longer actionable; its only currently supported kind is `solana_transaction_request`.

The Solana Pay POST requires a canonical payer `account` and ignores unknown request fields as required for forward-compatible protocol extensions. The optional GET `icon` must be an absolute credential-free HTTP(S) image URL controlled by the merchant.

Every successful or failed HTTP response that contains or may contain an action/capability must set `Cache-Control: no-store, no-cache`, `Pragma: no-cache`, `Referrer-Policy: no-referrer`, and `X-Content-Type-Options: nosniff`. The package router applies these headers to create/get/candidate/cancel/action responses and to both public transaction-request methods. A host endpoint that wraps CommerceXL checkout responses outside this router must apply the same policy. Package errors use `{"code": "...", "message": "..."}`; invalid, expired, malformed, Unicode, or oversized capabilities are the same safe 404. RPC outages are 503 with `Retry-After: 5`; per-intent issuance and candidate-check limits are 409. A candidate signature is claimed and committed before RPC. The default lifetime limit is `max_candidate_checks_per_intent=16`; an exact duplicate is idempotent and performs no immediate RPC read.

Create the background service with `create_sqlalchemy_reconciler(...)`. Schedule bounded calls to `reconcile_pending(limit=config.max_reconciliation_batch, now=clock.now())`. Do not add frontend polling: publish `CommercePaymentUpdatedEvent` through the host shared WebSocket after committed state changes and invalidate the payment/order queries.

Version 0.2.2 removes `signature_page_size` and `max_signature_pages`. Configure `max_candidate_verifications_per_intent=16` and `max_candidate_verifications_per_pass=32`. The global value must cover at least one complete per-intent budget. Automatic discovery performs one `getSignaturesForAddress` call with `per_intent + 1` entries and never follows a cursor. More qualifying signatures park the intent with `reference_candidate_budget_exceeded`; active payments move to review, while paid/cancelled/expired financial states remain unchanged. Operator tooling should verify an explicitly supplied signature instead of paginating the poisoned reference index.

The host migration must include non-null indexed `commerce_solana_payment_intent.reconcile_until` with `reconcile_until > expires_at`, and non-null `commerce_solana_transaction_issuance.issued_context_slot >= 0`. New intents snapshot `reconcile_until = expires_at + terminal_reconciliation_grace`. Do not derive either fact during later reconciliation.

## RPC and clusters

RPC is an injectable standard HTTP JSON-RPC client. Configure multiple HTTPS endpoints or a self-hosted node. Loopback HTTP is accepted only for `localhost`, `127.0.0.1`, and `[::1]` in development. Cluster names are restricted to `mainnet-beta`, `devnet`, and `testnet`, and must match their exact genesis hash.

No RPC API fee is required by the protocol. Public endpoints have rate limits and no availability SLA, so production should configure redundancy and monitor `UNKNOWN`, budget exhaustion, quarantine and backlog metrics. With a public endpoint, start with a host batch size of 25; the package additionally caps expensive candidate preparations globally.

## Local package development

From this package:

```bash
uv sync --frozen
uv run pytest tests
uv build
```

From a host repository:

```bash
uv pip install --python .venv --editable ../../orcestr-commerce-solana/backend
```

Never point production dependency metadata at a developer filesystem path. Release artifacts must include `LICENSE`, `NOTICE`, and `TRADEMARKS.md`.
