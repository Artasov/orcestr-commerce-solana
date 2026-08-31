# Security Policy

[Русская версия](./SECURITY.ru.md)

## Supported versions

Security fixes are handled on the default branch while all packages remain pre-1.0.

## Reporting a vulnerability

Do not open a public issue for vulnerabilities. Report security problems privately to the
maintainer through GitHub. Include the affected package/version, attack scenario, required
privileges, cluster and token program, expected and observed state transitions, and a minimal
reproduction when possible. Never include real credentials, capabilities, private keys, seed
phrases or production signed transactions.

## Sensitive areas

Extra review is required for changes involving order ownership, CSRF, capability disclosure,
idempotency, exact amount conversion, mint and recipient validation, transaction decoding,
Token-2022 extensions, commitment/finality, duplicate signatures, late payments, RPC failover,
row locking, outbox delivery, order execution, reconciliation and release artifacts.

The complete trust model is documented in [docs/threat-model.md](./docs/threat-model.md).
