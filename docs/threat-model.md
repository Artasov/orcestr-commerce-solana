# Threat model

The payer controls the browser, wallet, submitted signature and all request fields. Public RPC
nodes may be unavailable, stale, rate-limited or malicious. Token metadata is untrusted remote
content. Host administrators control registered assets and recipients, but changing them must
not alter old payment evidence.

The verifier and CommerceXL runtime protect against amount/mint/recipient substitution, fake
tokens with the same symbol, signature reuse, one transaction assigned to two orders, foreign
orders, CSRF, capability leakage, replay, late payment, cancel-versus-confirm races, forks,
unsupported transaction versions, malicious metadata URIs and resource-exhaustion scans.

Resource bounds are enforced at issuance and reconciliation: a row-locked per-intent issuance
limit is checked before RPC, reference history has bounded pages and an immutable slot lower bound,
and one signature's RPC material is reused across issuance comparisons. Capability input has an
exact fixed URL-safe shape and is mapped to a uniform private error response before hashing.

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
