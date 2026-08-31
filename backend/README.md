# Orcestr Commerce Solana for Python

`orcestr-commerce-solana` adds non-custodial native SOL and Token-2022 checkout to CommerceXL. It verifies raw finalized Solana transactions through standard JSON-RPC and does not require a paid API, webhook provider, private key, or custody service.

The first release deliberately rejects the legacy SPL Token Program, swaps, CPI transfers, batch payments, partial payments, transfer-fee mints, and unknown Token-2022 extensions. Correctness comes from the raw transaction and an immutable settlement snapshot. Public RPC endpoints remain rate-limited infrastructure without an availability SLA; applications can inject any compatible HTTP RPC endpoint or their own node without changing payment contracts.

## Install

```bash
pip install orcestr-commerce-solana
```

For local ecosystem development from the Orcestr backend:

```bash
uv pip install --python .venv --editable ../../orcestr-commerce-solana/backend
```

The host application owns the database engine, sessions, Alembic migrations, authentication, authorization, CSRF policy, scheduler, WebSocket transport, treasury configuration, and product pricing. Import `orcestr_commerce_solana.models` before collecting `CommerceBase.metadata`; the package intentionally ships no migrations.

## Minimal verifier

```python
from orcestr_commerce_solana import HttpSolanaRpc, SolanaTransactionVerifier
from orcestr_commerce_solana.config import SolanaRpcConfig
from orcestr_commerce_solana.constants import MAINNET_GENESIS_HASH

rpc = HttpSolanaRpc(
    SolanaRpcConfig(
        genesis_hash=MAINNET_GENESIS_HASH,
        endpoints=("https://api.mainnet-beta.solana.com",),
    ),
)
verifier = SolanaTransactionVerifier(rpc, used_signatures=my_database_signature_registry)
```

Build a `VerificationRequest` from the persisted intent and issuance snapshots, then call `await verifier.verify(request)`. `confirmed` is always provisional; only a fully decoded `finalized` transaction produces authoritative `MATCH`. `UNKNOWN` is retryable and must never grant a product. `REVIEW` preserves an on-chain mismatch for reconciliation.

See the repository documentation for the state machine, asset activation, transaction-request endpoint, free RPC operations, CommerceXL registration, FastAPI ports, and threat model.

The default host wiring is intentionally small:

- `SolanaApplicationService.build_default(...)` supplies authenticated checkout orchestration.
- `SolanaFastApiRouterFactory` supplies typed routes; the host injects Orcestr Auth actor, ownership, and CSRF dependencies.
- `create_sqlalchemy_reconciler(...)` supplies leased background reconciliation, DB-backed signature uniqueness, paginated reference scans, and safe expiry.
- `SolanaProviderRegistrationFactory` registers the provider explicitly in CommerceXL 0.3.2 or newer. Version 0.3.2 is the minimum because payment options carry the immutable order amount/currency snapshot and its state machine permits a provisional confirmed payment to expire after a complete final reference scan.

`SolanaProviderDependencies` requires a host `SettlementPriceKeyResolver`. It must resolve a stable product/plan/pack code from the order; broad order kinds are intentionally not used as a pricing fallback. For products priced in the database in the same currency as the selected asset, use the recommended exact strategy:

```python
from orcestr_commerce_solana import OrderSnapshotSettlementQuoteProvider

quotes = OrderSnapshotSettlementQuoteProvider(
    {"solana_orcestr": "ORCESTR"},
    version="catalog-v1",
)
```

The provider requires `order.currency == configured currency` for the exact validated asset option, converts the human decimal order amount with `SolanaAmountCodec`, rejects fractional precision instead of rounding, and validates positive u64 plus asset min/max bounds. Asset identity remains the validated option/mint; the display symbol is never a security input. Its immutable quote uses `source="order_snapshot"` and `rounding="exact"`. `FixedSettlementQuoteProvider` remains a separate strategy for intentionally precomputed raw prices keyed by `(resolved_price_key, asset_option_id)`; it is not the recommended catalogue-pricing path. A custom `RecipientResolver` supports either a Beauty treasury or host-verified P2P recipients without changing the verifier.

Transaction issuance is bounded by `max_issuances_per_intent` (default 16) under the intent row lock. `expires_at` is the public payment deadline; a successful complete scan expires the payment at that deadline. An immutable `reconcile_until` grace horizon keeps cancelled/expired attempts discoverable only for late finalized evidence. Every distinct late transfer is written to CommerceXL with the same terminal state and never grants the product.

Detailed integration and security contracts are in [architecture](https://github.com/Artasov/orcestr-commerce-solana/blob/main/backend/docs/architecture.md), [host integration](https://github.com/Artasov/orcestr-commerce-solana/blob/main/backend/docs/integration.md), and [security](https://github.com/Artasov/orcestr-commerce-solana/blob/main/backend/docs/security.md).

## Development

```bash
uv sync --frozen
uv run pytest tests
uv build
```

Python 3.12, 3.13, and 3.14 are supported. All timestamps are timezone-aware UTC and all blockchain amounts cross API boundaries as integer strings.

`TransactionVersion.LEGACY` denotes Solana's legacy wire-message format for verification only. The package emits v0 transactions, and it does not expose the legacy SPL Token Program as a supported public root API.
