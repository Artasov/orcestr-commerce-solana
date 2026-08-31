# Payment verification

Correctness does not depend on a commercial API. Any standards-compliant Solana JSON-RPC node
can provide the transaction bytes and status needed by the verifier. Public endpoints are
useful for development and low-volume operation, but have rate limits and no availability SLA;
an application may configure several free endpoints or its own node without changing payment
contracts.

## Candidate discovery

The connected-wallet flow posts the returned signature to an authenticated, payment-owned
endpoint; the verifier still accepts only a message issued for that intent and payer. For
cross-device QR, a worker may scan `getSignaturesForAddress(reference)`. A
subscription can reduce latency but is never authoritative: HTTP backfill is still required
after disconnects. Discovery is bounded by the oldest persisted `getLatestBlockhash` context slot
and configured history page limits. The optional RPC block time and host clock are not pagination
boundaries.

## Required checks

For every candidate, the backend obtains signature status and the raw transaction with a
supported transaction-version limit. It then verifies all of the following:

- the configured cluster genesis hash, valid Ed25519 transaction signature and exact persisted transaction issuance;
- successful transaction execution at Solana `finalized` commitment;
- the unique candidate signature has not been assigned to another payment;
- the reference account belongs to the same intended instruction/message;
- one supported top-level transfer targets the frozen merchant account;
- SOL uses the System Program, or token payment uses only the Token-2022 Program;
- Token-2022 `TransferChecked` carries the exact mint, destination, raw amount and decimals;
- pre/post balances prove the exact recipient delta;
- no CPI, swap, batch, partial, underpayment or overpayment is accepted as the simple payment;
- the normalized transaction falls within the issuance acceptance-time policy.

The Legacy Token Program is rejected even when mint, recipient and amount otherwise appear
correct. Unknown instructions, unsupported extensions and ambiguous multi-transfer
transactions go to review rather than success.

## Result semantics

RPC timeout, `429`, a temporarily missing transaction or insufficient finality maps to a
retryable `UNKNOWN`/pending result. A correctly decoded transaction at `confirmed` remains an
intermediate state and schedules another check. A cryptographically valid mismatch maps to a stable reject
or review reason. Only complete `finalized` evidence maps to paid. A late valid payment
is recorded for reconciliation but does not silently reactivate an expired order.

One failed/review issuance is audit evidence, not the verdict for the entire payment intent. The
worker continues across other issuances and later passes. RPC status and raw bytes are fetched once
per signature per pass, so RPC calls do not multiply by the issuance count. Payment product effects
are allowed only before public `expires_at`; after a complete expiry scan, every distinct finalized
transfer is attached to the unchanged expired/cancelled CommerceXL payment during the bounded grace
period. Persisted signatures are excluded from later passes, so one late transfer cannot hide another.

Verifier calls are idempotent. Persisting evidence and asking CommerceXL to apply a verification
result occurs under row locks and unique constraints so concurrent discovery, retries and event
redelivery cannot execute a product twice.

## ORCESTR fixture

The reference mainnet asset is mint
`HztMC7xr2j6ngpfWbYsF5xnRZkgouDXQ5rVis3C3pump`, Token-2022 program
`TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb`, 6 decimals. Tests use frozen RPC fixtures; they do
not require mainnet availability. A real CPI/swap fixture is retained as a negative regression
case and must resolve to an unsupported-CPI/swap reason, never payment success.

## Protocol references

- [Solana Pay specification](https://docs.solanapay.com/spec)
- [Solana payment verification tools](https://solana.com/docs/payments/accept-payments/verification-tools)
- [Solana JSON-RPC documentation](https://solana.com/docs/rpc)
- [Solana cluster identities](https://solana.com/docs/references/clusters)
- [Solana payment production readiness](https://solana.com/docs/payments/production-readiness)
