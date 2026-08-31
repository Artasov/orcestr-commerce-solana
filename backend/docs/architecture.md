# Architecture

## Trust boundaries

CommerceXL owns orders, payment attempts, idempotency, and the product effect. This package owns immutable Solana settlement and issuance snapshots, capability digests, transfer evidence, and provider audit events. The host owns authentication, authorization, CSRF, database sessions, treasury/P2P recipient policy, product quotes, scheduling, and WebSocket delivery.

The browser never chooses a mint, recipient, decimals, raw amount, cluster, or commitment. It selects an order-specific option published by the server. `SettlementPriceKeyResolver`, `RecipientResolver`, and `SettlementQuoteProvider` are rerun during creation after availability preflight. The price-key resolver must return a stable product/plan/pack identity; a broad order kind is never used implicitly.

## Checkout

1. CommerceXL asks the explicit Solana provider for options for one order and actor.
2. The provider snapshots an activated asset, recipient, canonical recipient-policy version, exact raw amount, display metadata, quote provenance, random reference, and mandatory `finalized` commitment.
3. Only a SHA-256 digest of the 43-character, 256-bit, short-lived capability is stored. The canonical transaction-request URI is returned and can be reissued through `get_action()` while the intent is exactly `waiting`.
4. The wallet POSTs its payer public key to the transaction-request URL. Under an intent row lock, the service enforces `max_issuances_per_intent`, creates a minimal v0 transaction with a unique non-authoritative issuance memo, and stores the exact message digest, payer, blockhash, block-height boundary, `getLatestBlockhash` context slot, and acceptance time before responding. The issuance memo keeps repeated requests from the same payer distinct even when RPC returns the same blockhash.
5. A connected wallet may submit a signature as an untrusted acceleration hint. Reference-address history is the independent recovery path.

Once exact chain evidence reaches `observed` or `confirmed`, all outstanding capabilities are revoked and no new action or issuance is allowed. A client-submitted processed signature alone does not change the intent from `waiting`.

There is no static `solana:` transfer-request fallback in v0.1. Every supported payment is an issued `solana_transaction_request`.

## Verification

The verifier downloads raw base64 transaction bytes from standard JSON-RPC and accepts exactly one top-level payment instruction:

- System Program transfer for native SOL; or
- Token-2022 `TransferChecked` for a configured mint.

The verifier checks the Ed25519 transaction signature locally. The instruction must include the unique reference account and exactly match the issued payer, message digest, blockhash, transaction version, recipient, mint, raw amount, and decimals. Recipient balance deltas must equal the requested amount. Inner instructions, CPI, swaps, multiple payment instructions, partial payments, and the legacy Token Program are rejected. An exact finalized transfer outside its immutable acceptance window is normalized first and then returned as review evidence.

`UNKNOWN` represents rate limiting, unavailable/null RPC results, or other retryable uncertainty. It never grants a product. `CONFIRMED` persists evidence and schedules another check. Only `FINALIZED` produces `MATCH`, which CommerceXL applies atomically with the product effect.

## Reconciliation and expiry

Workers lease due rows with `FOR UPDATE SKIP LOCKED`. Candidate hints and paginated `getSignaturesForAddress` history are deduplicated. History is scanned in bounded pages until it crosses the oldest persisted `issued_context_slot`; host clocks and optional RPC `blockTime` are not used as the pagination boundary. Reaching the configured page limit is incomplete, not proof of non-payment. Status and raw transaction RPC material is fetched once per signature in each pass, then compared locally with at most the configured number of issuances.

`expires_at` is the public decision deadline and is never extended to the evidence grace horizon. After a complete reference scan at or after that deadline, any active intent becomes `expired`, including one with provisional observed/confirmed evidence. Every new finalized exact transfer found in that pass or during the immutable `reconcile_until` grace is persisted through CommerceXL with the same `expired`/`cancelled` state, its own outbox/audit evidence, and `product_effect=false`; previously recorded signatures are skipped deterministically, so one late transfer cannot starve a second. None of them silently grants the product. An incomplete history scan caused by RPC outage or the page bound schedules a retry instead of guessing. Terminal mutations retain a short recovery lease until the reconciler explicitly finishes the complete pass, preventing a worker crash between expiry and multiple evidence writes from making the row undiscoverable. After a complete scan at the grace horizon, provisional/null results no longer keep the terminal row scheduled forever.

Failed or review evidence is scoped to the exact issuance. It is deduplicated in the audit trail and cannot terminalize the whole intent or prevent a later valid issuance from settling before `expires_at`.

A payer can sign more than one already-issued transaction before the first becomes observed. After the primary `MATCH`, the row therefore stays scheduled until `reconcile_until`. Reference-only scans exclude the primary signature and persist every additional exact finalized match as `solana.payment.duplicate_payment` with `product_effect=false`; the original verified signature and public `paid` state stay unchanged. This audit path deliberately does not call CommerceXL `apply_verification(PAID)`, because a new paid evidence payload must never re-enter product execution.

## Client state

Authenticated REST state is authoritative. The shared realtime invalidation event is:

```json
{
  "event": "commerce.payment.updated",
  "order_public_id": "uuid",
  "payment_public_id": "uuid",
  "revision": 3
}
```

Routing `user_id` is transport metadata and is not part of this payload.
