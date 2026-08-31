# Changelog

All notable changes to Orcestr Commerce Solana are documented in this file.

## 0.2.1 - 2026-08-31

- Settle an exact finalized transfer submitted within its issuance acceptance window when reference reconciliation discovers it after public intent expiry but before the immutable reconciliation horizon.
- Keep exact confirmed evidence pending for finality after public expiry, bounded by the immutable reconciliation horizon.
- Prevent an incomplete reference-history scan from starving an already verified finalized match.
- Preserve fail-closed terminal evidence without product effects when final proof arrives at or after the reconciliation horizon.

## 0.2.0 - 2026-08-31

- Add an exact order-snapshot quote strategy for CommerceXL database prices denominated in the selected Solana asset.
- Require explicit quote rounding provenance and add the `exact` mode without an implicit legacy default.
- Carry mandatory CommerceXL 0.3.2 order amount and currency snapshots in frontend payment options.
- Keep fixed raw quotes as a separate strategy for deliberately precomputed settlement amounts.
- Accept exact database-scale amounts whose excess fractional digits are zero.

## 0.1.0 - 2026-08-31

- Add a non-custodial native SOL and Token-2022 payment backend for CommerceXL.
- Add framework-independent, React, and Orcestr UI frontend packages.
- Add strict raw-transaction verification over standard Solana JSON-RPC.
- Reject the Legacy Token Program, CPI/swaps, ambiguous transfers and unsafe Token-2022 assets.
- Add transaction-request, QR, wallet, reconciliation, and integration documentation.
