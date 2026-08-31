# Operations and reconciliation

Payment correctness and RPC availability are separate concerns. Verification always reads and
decodes standard on-chain data, while production availability is improved through configured
endpoint failover, bounded exponential backoff, jitter, health checks and manual recheck.

## Runtime jobs

Use one scheduled batch reconciliation job, not one polling job per payment. It selects due
attempts in bounded pages, claims rows safely, scans only the allowed slot range, applies
verification results through CommerceXL and schedules the next attempt with backoff. Outbox
delivery occurs after database commit and can be retried independently.

Keep `max_issuances_per_intent`, signature page size, page count and batch size bounded. Each
reconciliation pass fetches status/raw transaction once per discovered signature and compares it
locally against the bounded issuance set. The history lower bound is the immutable
`issued_context_slot`, not application time or optional RPC `blockTime`.

## Health and metrics

Monitor cluster genesis, current finalized slot and lag for each endpoint, rate limits, RPC
errors, oldest pending attempt, reconciliation backlog, state-transition counts, time to
observed/confirmed/paid, duplicate signatures and finalization failures. Here `paid` means the
transaction was finalized and the CommerceXL product effect committed atomically. Logs correlate by
payment public id, reference digest and signature; never log raw capability tokens, signed
transaction bodies, auth cookies or seed material.

## Manual reconciliation

Host-owned operator tooling should support search by order, payment public id, reference or
signature and comparison of the immutable snapshot with decoded evidence. Any host operation
that requests a recheck, cancels an unpaid attempt, or records a manual resolution must capture
an actor and reason; the package does not expose an unauthenticated admin API. Changing an asset
or recipient configuration never rewrites an existing snapshot.

For Token-2022 settlement, provision the recipient's associated token account before enabling
the asset. The account must be the canonical Token-2022 ATA for the configured wallet and mint,
be initialized on the pinned cluster, and pass the extension policy. Version 0.1 does not create
or rent-fund a recipient ATA during checkout because the payment service holds no treasury key.

At `expires_at`, a complete scan moves the public payment to expired. If any issuance exists,
`next_check_at` remains scheduled through the immutable `reconcile_until` grace; cancellation uses
the same terminal scan. Every distinct finalized exact transfer discovered in this period is recorded
through CommerceXL with the unchanged terminal state and its own outbox/manual-alert event. Do not silently
grant the product, reuse it for another order or automatically refund it in 0.1. After a complete
scan at the horizon, clear `next_check_at` even if only provisional status remains.

Paid attempts also remain scheduled only until `reconcile_until`, because two transactions may
have been issued and signed before the first observation. Additional finalized matches produce a
duplicate-payment transfer/event and an operator signal while preserving the original primary
signature and public paid state. They never execute the order again; operators decide refund or
credit policy outside v0.1.
