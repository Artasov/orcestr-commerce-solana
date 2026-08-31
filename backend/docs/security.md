# Security contract

## Supported scope

- native SOL through the System Program;
- Token-2022 mints activated against exact cluster genesis, owner, decimals, authorities, account hash, and an allowlisted extension policy;
- direct top-level transfers compiled by the transaction-request service;
- mandatory finalized settlement, with confirmed evidence retained only while retrying.

The legacy SPL Token Program is always explicitly rejected. There is no compatibility mode.

## Default-deny cases

The verifier rejects CPI/inner instructions, swaps, unknown programs, batch or multiple transfers, partial/overpayments, wrong references, wrong recipients, wrong mints, wrong decimals, wrong balance deltas, reused signatures, message/issuance mismatches, and transfers outside the issuance window.

Raw `getTransaction` bytes must be non-empty and at most the 1232-byte Solana wire packet limit. The HTTP adapter bounds base64 length before decoding and the normalized RPC model repeats the byte bound before solders parsing.

Transfer-fee mints and recipient accounts that require MemoTransfer are rejected in the current release. Unknown or dangerous Token-2022 extensions are not activated merely because a client requests the mint. Display symbol/name are non-authoritative snapshot fields; identity is cluster genesis plus mint/program.

The immutable settlement also records `recipient_policy_version` from the host resolver. This is audit provenance for treasury or P2P selection; verification continues to use the exact snapshotted recipient address rather than trusting the version label.

## Operational requirements

- Inject a DB-backed `UsedSignatureRegistry` in production. The public verifier has no permissive default.
- Preserve the unique `(cluster, signature)` database constraint and apply evidence plus CommerceXL verification in one transaction.
- Treat candidate signatures as hostile hints. A mismatch must not move an intent to review or stop the search for later valid evidence.
- Bound transaction creation with `max_issuances_per_intent` under the intent row lock before any blockhash RPC or insert. Keep lock order intent then capability.
- Cancel through the addon application service only. It takes the Solana intent lock before CommerceXL order/payment locks; the provider rejects an unprepared generic CommerceXL cancellation to avoid inverse-order deadlocks.
- Treat 429, null transaction/status, timeout, and endpoint outage as retryable `UNKNOWN`.
- Continue bounded reference scans after the primary payment. Additional exact finalized signatures are duplicate-payment audit evidence only and must never call the product effect again.
- Accept capability tokens only in the exact generated 43-character URL-safe alphabet. Redact their paths from proxy/access/error logs; only their digest belongs in the database. All responses that may contain them are private and non-cacheable.
- Never place a merchant private key in this package. Recipients receive funds directly.
- Keep authentication and wallet ownership separate. Payment verification does not prove application identity; P2P resolution must consume a host-verified binding.
