# Orcestr Commerce Solana

[Русская версия](./README.ru.md)

Orcestr Commerce Solana is a non-custodial Solana payment extension for
[CommerceXL](https://github.com/Artasov/commercexl). It creates wallet actions, verifies raw
transactions through standard Solana JSON-RPC, and lets a host application finalize a
CommerceXL order exactly once after mandatory Solana `finalized` commitment.

The first release intentionally supports native SOL and allowlisted **Token-2022** fungible
tokens only. The legacy SPL Token Program is rejected. No paid RPC, webhook, indexer, hosted
checkout, or server-side private key is required for payment correctness.

## Packages

| Package | Purpose |
| --- | --- |
| `orcestr-commerce-solana` | Python domain services, RPC verifier, SQLAlchemy/FastAPI adapters and reconciliation runtime |
| `@orcestr/commerce-solana-core` | Framework-independent API contracts, parsers, Solana URI helpers and client |
| `@orcestr/commerce-solana-react` | React Query hooks, wallet-standard runtime and shared event integration |
| `@orcestr/commerce-solana-ui` | Accessible checkout, QR, waiting, result and recovery views built on `@orcestr/ui` |

The backend integrates authentication through injected dependencies. It does not create a
second authentication subsystem: an Orcestr application supplies its existing Orcestr Auth
actor, ownership, permission and CSRF dependencies. The frontend similarly receives the host
`QueryClient`, authenticated fetch implementation and shared event source.

## Security boundaries

- The exact cluster, genesis hash, recipient, mint, Token-2022 program, decimals, integer raw
  amount, reference and expiry are frozen in a server-side payment snapshot.
- A token is identified by its mint address, never by its name or symbol.
- A candidate signature is not proof of payment. The backend decodes the raw finalized
  transaction and validates its status, instruction, accounts, amount, reference and balance
  delta before CommerceXL may execute the order.
- Unknown RPC state, rate limiting and temporary network failures remain retryable; they never
  become a successful or failed payment by guesswork.
- The payer signs directly in their wallet and sends funds to the server-resolved recipient.
  The library stores no seed phrase or recipient private key; a host may resolve a treasury or
  a verified per-user P2P address.

See [architecture](./docs/architecture.md), [verification](./docs/verification.md),
[local development](./docs/local-development.md), [operations](./docs/operations.md), and
[release process](./docs/releases.md).

## ORCESTR reference asset

The Beauty pilot uses ORCESTR mint
[`HztMC7xr2j6ngpfWbYsF5xnRZkgouDXQ5rVis3C3pump`](https://pump.fun/coin/HztMC7xr2j6ngpfWbYsF5xnRZkgouDXQ5rVis3C3pump)
on Solana mainnet. On-chain inspection shows
Token-2022 ownership, 6 decimals, metadata name `Orcestr`, symbol `ORCESTR`, and disabled
mint/freeze authorities. Applications must still register it by exact mint and cluster in an
explicit allowlist.

## Development

```bash
cd backend
uv sync --frozen
uv run pytest -q
uv build

cd ../frontend
npm ci
npm test
npm run pack:dry-run
```

Run targeted checks while developing. Consumer applications own their database migration and
must import the add-on models into CommerceXL metadata before Alembic compares schemas.

## Status

The project is in alpha. APIs may change between minor versions until the first stable release.
Read [SECURITY.md](./SECURITY.md) before production use.

## License

Code and documentation are licensed under MPL-2.0. Brand use is governed by
[TRADEMARKS.md](./TRADEMARKS.md).
