# Orcestr Commerce Solana

[English version](./README.md)

Orcestr Commerce Solana — некастодиальное дополнение к
[CommerceXL](https://github.com/Artasov/commercexl) для платежей в сети Solana. Библиотека
создаёт действия для кошелька, проверяет сырые транзакции через стандартный Solana JSON-RPC и
разрешает приложению исполнить заказ CommerceXL ровно один раз только после обязательного
commitment `finalized`.

Первая версия намеренно поддерживает только native SOL и разрешённые fungible-токены
**Token-2022**. Legacy SPL Token Program всегда отклоняется. Для корректной проверки не нужны
платный RPC, webhook, индексатор, hosted checkout или приватный ключ на сервере.

## Пакеты

| Пакет | Назначение |
| --- | --- |
| `orcestr-commerce-solana` | Python-сервисы, RPC verifier, SQLAlchemy/FastAPI adapters и reconciliation runtime |
| `@orcestr/commerce-solana-core` | Независимые от фреймворка контракты API, parsers, Solana URI helpers и client |
| `@orcestr/commerce-solana-react` | React Query hooks, wallet-standard runtime и подключение общих событий приложения |
| `@orcestr/commerce-solana-ui` | Доступные checkout-, QR-, waiting-, result- и recovery-компоненты на `@orcestr/ui` |

Backend получает аутентификацию через dependency injection. В приложении Orcestr используются
существующие actor/ownership/permission/CSRF dependencies Orcestr Auth, а не второй auth
runtime. Frontend получает от host-приложения его `QueryClient`, authenticated fetch и общий
источник WebSocket-событий.

## Границы безопасности

- Backend фиксирует в payment snapshot точные cluster/genesis hash, recipient, mint,
  Token-2022 program, decimals, целое количество base units, reference и expiry.
- Токен определяется только mint address, но не названием или symbol.
- Signature сама по себе не доказывает оплату. Backend декодирует сырую finalized-транзакцию и
  проверяет status, instruction, accounts, amount, reference и balance delta.
- Неизвестный результат RPC, rate limit и временный сетевой сбой остаются повторяемым состоянием
  и никогда не превращаются в успешную оплату по догадке.
- Пользователь подписывает перевод в своём кошельке и отправляет средства получателю,
  разрешённому backend-ом; библиотека не хранит seed phrase или private key, а host может
  разрешать treasury либо подтверждённый P2P-адрес пользователя.

Подробности: [архитектура](./docs/architecture.md),
[верификация](./docs/verification.md), [локальная разработка](./docs/local-development.md),
[эксплуатация](./docs/operations.md) и [релизы](./docs/releases.md).

## Эталонный ORCESTR asset

Пилот Beauty использует mainnet mint ORCESTR
[`HztMC7xr2j6ngpfWbYsF5xnRZkgouDXQ5rVis3C3pump`](https://pump.fun/coin/HztMC7xr2j6ngpfWbYsF5xnRZkgouDXQ5rVis3C3pump).
Проверка on-chain данных показала Token-2022,
6 decimals, metadata `Orcestr`/`ORCESTR` и отключённые mint/freeze authorities. Приложение всё
равно обязано зарегистрировать точный mint и cluster в явном allowlist.

## Разработка

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

Во время разработки запускаются целевые проверки. Миграцией владеет consumer-приложение: оно
должно импортировать модели дополнения в metadata CommerceXL до сравнения схем Alembic.

## Статус и лицензия

Проект находится в alpha. До стабильного релиза публичные API могут меняться между minor
версиями. Перед production-применением прочитайте [SECURITY.ru.md](./SECURITY.ru.md).
Код и документация распространяются по MPL-2.0, использование бренда регулирует
[TRADEMARKS.md](./TRADEMARKS.md).
