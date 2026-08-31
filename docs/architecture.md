# Architecture

Orcestr Commerce Solana is an add-on, not a CommerceXL fork. CommerceXL remains responsible
for catalogue pricing, orders, payment-attempt lifecycle, idempotent product execution and
outbox events. This repository owns Solana settlement snapshots, wallet actions, raw
transaction verification and Solana-specific reconciliation evidence.

## Package boundaries

The Python package contains domain types and services that do not import an Orcestr
application. Optional SQLAlchemy and FastAPI adapters depend only on explicit ports. The host
provides its CommerceXL runtime, control-database session factory, authenticated actor,
ownership and permission checks, clock, RPC endpoints, merchant recipient resolver and event
publisher.

`@orcestr/commerce-solana-core` is framework-independent. React Query and wallet-standard
integration live in `@orcestr/commerce-solana-react`. Rendered checkout surfaces live in
`@orcestr/commerce-solana-ui`, where `@orcestr/ui` is a peer dependency. No package creates a
second auth provider, query client, WebSocket connection or polling loop.

## First-release data flow

1. An authenticated user creates a server-priced CommerceXL order. Each catalogue currency is a separate database price row, and the order freezes the selected human decimal amount and normalized currency.
2. The host lists server-configured payment options. A client selects only an opaque option id.
3. CommerceXL creates an idempotent payment attempt and invokes the Solana provider.
4. The provider freezes the settlement snapshot and creates a unique reference plus an expiring
   transaction-request capability.
5. The browser opens a connected wallet or displays the same action as a QR code.
6. A submitted signature is only a candidate. The backend reads status and raw transaction via
   standard JSON-RPC, decodes it locally and persists evidence.
7. At mandatory `finalized` commitment, CommerceXL locks the attempt and order, records the unique signature,
   executes the product effect once, and writes an outbox event in the same database transaction.
8. A bounded post-paid reference scan records any second issued-and-signed payment as duplicate evidence without executing the product again.
9. The host publishes the committed event through its existing WebSocket; React Query
   invalidates the order/status query.

## Settlement snapshot

The immutable snapshot records cluster and genesis hash, recipient owner and token account,
the host recipient-policy version,
asset kind, exact mint, Token-2022 program id, decimals, expected integer raw amount, reference,
quote provenance, expiry and the mandatory `finalized` policy. Display labels, metadata URIs and symbols are
informational and are never used to verify value.

For database-priced orders denominated in the selected asset, the recommended order-snapshot
quote provider converts the already frozen CommerceXL decimal amount directly to raw units. It
requires an explicit per-option commerce-currency allowlist and exact currency equality,
supported fractional precision, positive u64 and configured option bounds. The quote records
`rounding="exact"`; absence of rounding provenance is invalid. Fixed raw quotes remain a separate
strategy for amounts computed before checkout, not a duplicate product-price store.
The validated option and mint define asset identity; display symbols remain informational.

Token-2022 assets enter the runtime only through an explicit registry. Registration reads the
mint from the configured cluster and rejects a wrong owner, decimals mismatch, active
mint/freeze authority, or unsupported value-changing extension. Existing payment snapshots do
not change when registry configuration changes.

The recipient comes from an injected server-side resolver and may vary per intent. The Beauty
pilot resolves one treasury wallet, while a P2P host can resolve a verified wallet belonging to
the recipient user without changing the verifier. A client-provided address is never accepted
without the host's ownership and policy checks.

For Token-2022 the resolver returns a pre-provisioned canonical recipient ATA. Asset activation
checks its mint, owner, Token-2022 program and extensions; checkout never creates that account or
uses a server-side signing key.

## Database ownership

Solana ORM models that reference CommerceXL inherit the public `CommerceBase`, so the host sees
one metadata graph. The host application imports add-on models and owns the Alembic migration.
In Orcestr, platform commerce and Solana payment rows use the control database; tenant/domain
sessions are not mixed into this first integration.

## Explicit non-goals for 0.1

The first release does not provide custody, swaps, price discovery from Pump.fun, transfer-fee
tokens, partial or split payment, recurring debit, automatic refund, fee sponsorship, Legacy
Token Program compatibility, or a second realtime transport. These can be designed later
without weakening the base verifier.
