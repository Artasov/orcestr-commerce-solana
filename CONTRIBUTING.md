# Contributing

[Русская версия](./CONTRIBUTING.ru.md)

Orcestr Commerce Solana is a public payment foundation extracted from Orcestr product work.
Changes must preserve an explicit trust boundary between the wallet, host application,
CommerceXL runtime, RPC node and on-chain evidence.

## Development

Backend checks:

```bash
cd backend
uv sync --frozen
uv run pytest -q
uv build
```

Frontend checks:

```bash
cd frontend
npm ci
npm test
npm run pack:dry-run
```

## Change checklist

Describe in the pull request:

- which public Python or npm contract changed;
- payment-state, idempotency, ownership and finalization implications;
- Solana cluster, token-program, mint-extension, instruction or transaction-version impact;
- schema and consumer-owned migration impact;
- English and Russian documentation or UI-copy updates;
- targeted tests and the consumer flow used for validation.

Never weaken Token-2022 validation to accept the legacy Token Program. Do not treat a wallet
callback, signature string, `confirmed` status or token symbol as sufficient proof of payment.
Never commit credentials, seed phrases, private keys, signed transaction bodies, capabilities,
production wallet data or local package paths.
