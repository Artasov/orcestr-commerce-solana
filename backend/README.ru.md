# Orcestr Commerce Solana для Python

`orcestr-commerce-solana` добавляет в CommerceXL некастодиальную оплату native SOL и Token-2022. Библиотека сама проверяет raw-транзакцию через стандартный Solana JSON-RPC и не требует платного API, webhook-провайдера, приватного ключа или хранения средств пользователя.

Первая версия намеренно отклоняет Legacy SPL Token Program, swap, CPI-переводы, batch-оплаты, частичные платежи, mint-ы с transfer fee и неизвестные Token-2022 extensions. Источник истины — raw-транзакция и неизменяемый settlement snapshot. Публичные RPC бесплатны, но ограничены по частоте запросов и не дают SLA; приложение может передать любой совместимый HTTP endpoint или собственную ноду без изменения платёжных контрактов.

## Установка

```bash
pip install orcestr-commerce-solana
```

Локальное подключение из backend Orcestr:

```bash
uv pip install --python .venv --editable ../../orcestr-commerce-solana/backend
```

Host-приложение владеет DB engine, сессиями, Alembic-миграциями, аутентификацией, авторизацией, CSRF, scheduler, WebSocket, treasury-настройками и ценами продуктов. До формирования `CommerceBase.metadata` импортируйте `orcestr_commerce_solana.models`. Пакет намеренно не содержит миграций.

## Минимальная проверка

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

Соберите `VerificationRequest` из сохранённых snapshot intent и issuance, затем вызовите `await verifier.verify(request)`. `confirmed` всегда остаётся промежуточным состоянием; только полностью декодированная `finalized`-транзакция даёт authoritative `MATCH` и разрешает выдачу продукта. `UNKNOWN` всегда означает повторяемую проверку и никогда не должен начислять товар. `REVIEW` сохраняет фактическое on-chain расхождение для reconciliation.

Готовая host-интеграция состоит из четырёх частей:

- `SolanaApplicationService.build_default(...)` реализует authenticated checkout flow.
- `SolanaFastApiRouterFactory` даёт typed routes; host внедряет Orcestr Auth actor, ownership и CSRF dependencies.
- `create_sqlalchemy_reconciler(...)` даёт lease для конкурентных workers, DB-проверку уникальности signature, пагинацию reference history и безопасное истечение intent.
- `SolanaProviderRegistrationFactory` явно регистрирует provider в CommerceXL 0.3.2 или новее. Версия 0.3.2 обязательна: payment option содержит неизменяемый snapshot суммы и валюты заказа, а state machine разрешает истечение предварительно подтверждённого платежа после полного финального scan по reference.

`SolanaProviderDependencies` требует host-реализацию `SettlementPriceKeyResolver`. Она должна вернуть стабильный код конкретного продукта, плана или pack; широкий `order.kind` намеренно не используется как fallback. Для продуктов с ценой в базе в той же валюте, что и выбранный asset, рекомендуется exact-стратегия:

```python
from orcestr_commerce_solana import OrderSnapshotSettlementQuoteProvider

quotes = OrderSnapshotSettlementQuoteProvider(
    {"solana_orcestr": "ORCESTR"},
    version="catalog-v1",
)
```

Provider требует `order.currency == configured currency` для конкретного validated asset option, преобразует human decimal заказа через `SolanaAmountCodec`, отклоняет лишнюю дробную точность без округления и проверяет positive u64 и asset min/max. Identity задаётся validated option/mint; display symbol никогда не участвует в security-проверке. В immutable quote фиксируются `source="order_snapshot"` и `rounding="exact"`. `FixedSettlementQuoteProvider` остаётся отдельной стратегией для заранее рассчитанных raw-цен с ключом `(resolved_price_key, asset_option_id)`, но не является рекомендуемым путём для каталожных цен. `RecipientResolver` одинаково поддерживает treasury Beauty и P2P-получателя, уже проверенного host-приложением.

Число transaction issuance ограничено `max_issuances_per_intent` (по умолчанию 16) под row lock intent. `expires_at` закрывает публичные действия оплаты. До `reconcile_until` reconciliation всё равно засчитывает exact finalized-перевод, если его on-chain `block_time` попадает в неизменяемое окно принятия issuance, а exact confirmed evidence ожидает finality. На границе этого горизонта и после неё proof сохраняется fail-closed как terminal evidence без выдачи продукта. Перевод вне окна принятия или перевод уже cancelled/expired intent также никогда не выдаёт продукт.

Подробности: [архитектура](https://github.com/Artasov/orcestr-commerce-solana/blob/main/backend/docs/architecture.md), [подключение к host](https://github.com/Artasov/orcestr-commerce-solana/blob/main/backend/docs/integration.md), [security contract](https://github.com/Artasov/orcestr-commerce-solana/blob/main/backend/docs/security.md).

## Разработка

```bash
uv sync --frozen
uv run pytest tests
uv build
```

Поддерживаются Python 3.12, 3.13 и 3.14. Все даты timezone-aware UTC, а blockchain amounts передаются через API строками целых чисел.

`TransactionVersion.LEGACY` означает только legacy wire-message format Solana для проверки. Пакет выпускает v0-транзакции и не экспортирует Legacy SPL Token Program как поддерживаемый root API.
