# Threat model

The payer controls the browser, wallet, submitted signature and all request fields. Public RPC
nodes may be unavailable, stale, rate-limited or malicious. Token metadata is untrusted remote
content. Host administrators control registered assets and recipients, but changing them must
not alter old payment evidence.

The verifier and CommerceXL runtime protect against amount/mint/recipient substitution, fake
tokens with the same symbol, signature reuse, one transaction assigned to two orders, foreign
orders, CSRF, capability leakage, replay, late payment, cancel-versus-confirm races, forks,
unsupported transaction versions, malicious metadata URIs and resource-exhaustion scans.

Resource bounds are enforced before RPC: a row-locked per-intent issuance limit and durable
candidate-claim limit are checked first. Reconciliation prepares at most 16 unique signatures per
intent and 32 per pass, and performs one reference request for 17 entries with no cursor. The extra
entry is an overflow sentinel: the intent is quarantined rather than treating a truncated window as
terminal. An immutable slot lower bound applies, and one signature's RPC material is reused across
issuance comparisons. Capability input has an exact fixed URL-safe shape and is mapped to a uniform
private error response before hashing.

Payment success requires locally decoded on-chain evidence at mandatory `finalized` commitment. Two
independent RPC responses may be compared for high-value payments, but a disagreement remains
unknown/review rather than choosing the favorable answer. Database unique constraints and row
locks enforce one signature assignment, one active attempt and one product execution.

The normal payment path is non-custodial. No server signer, seed phrase or refund key is stored
by the library. If a future host adds signing, it must use a separate signer interface, secret
manager, transaction policy, limits, approval and audit trail. That feature is outside 0.1.

Security-sensitive logs redact capabilities, auth headers, cookies, serialized signed
transactions and personal data. UI renders metadata as plain text and never fetches or executes
arbitrary mint-provided content as trusted application code.

Version 0.2.2 still bounds a replayed public transaction-request capability with
`max_issuances_per_intent`, and bounds capability rotation only by concurrently active rows. A leaked
capability can therefore consume the issuance allowance, while an authenticated actor can repeatedly
rotate actions. Hosts should rate-limit both routes by payment and actor and alert on quota pressure.
Durable lifetime counters for those two paths require a future schema contract; do not raise the
existing limits as a workaround.
