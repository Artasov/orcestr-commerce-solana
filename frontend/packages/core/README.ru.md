# @orcestr/commerce-solana-core

Независимые от фреймворка контракты и HTTP client Orcestr Commerce Solana. Версия 0.1 поддерживает native SOL и Token-2022 только через transaction request; статический transfer request и legacy Token Program отклоняются.

## Установка

```bash
npm install @orcestr/commerce-solana-core
```

## API client

Передайте существующий authenticated fetch приложения. Пакет не создаёт auth-сессию и не обновляет её самостоятельно.

```ts
import {
  SolanaCommerceClient,
  createAuthenticatedFetchExecutor,
  selectSolanaCommerceOptions,
} from "@orcestr/commerce-solana-core";

const client = new SolanaCommerceClient({
  baseUrl: "https://app.example.com/api/v1/",
  executor: createAuthenticatedFetchExecutor(authFetch),
});

const response = await client.listCommercePaymentOptions(orderPublicId);
const option = selectSolanaCommerceOptions(response)[0];
if (!option) throw new Error("Solana payment option отсутствует");

const payment = await client.createCommercePaymentAttempt({
  orderPublicId,
  paymentOptionId: option.id,
  idempotencyKey,
});
const intent = await client.getPaymentIntent(payment.id);
```

Это стабильный flow CommerceXL 0.3.1: order → provider-neutral payment options → payment attempt. `CommercePayment` остаётся каноническим статусом карточки заказа, а Solana intent добавляет immutable settlement snapshot для checkout. Для повторного открытия используйте `issueCommerceCheckoutAction(payment.id)` либо `issuePaymentIntentAction(payment.id)`: выпускается новая короткоживущая capability без замены payment/reference.

В интеграции с Orcestr Auth функция `authFetch` сохраняет общую cookie/OAuth-сессию, refresh, CSRF и typed API errors. Ответы с capability помечены `responseSensitivity: "capability"`; встроенный executor редактирует их HTTP error body, а parser errors не сохраняют отклонённые raw values. Custom executor также не должен логировать или сохранять capability URL/body.

Основные CommerceXL routes:

- `GET /commerce/orders/{order_public_id}/payment-options/`
- `POST /commerce/orders/{order_public_id}/payment-attempts/`
- `GET /commerce/payments/{payment_public_id}/`
- `POST /commerce/payments/{payment_public_id}/checkout-action/`

Solana detail routes:

- `GET /commerce/solana/orders/{order_public_id}/payment-options`
- `POST /commerce/solana/payment-intents`
- `GET /commerce/solana/payment-intents/{payment_public_id}`
- `POST /commerce/solana/payment-intents/{payment_public_id}/candidate-signatures`
- `POST /commerce/solana/payment-intents/{payment_public_id}/cancel`
- `POST /commerce/solana/payment-intents/{payment_public_id}/actions`

При другом mount prefix передайте собственный `SolanaCommerceRoutes`. Custom route обязан оставаться absolute path внутри настроенного API base path: traversal, protocol-relative path, backslash-нормализация и control characters отклоняются до запуска authenticated executor. Встроенные authenticated и public fetch executors запрещают HTTP redirects.

## Публичный transaction request

Для него используется отдельный executor с `credentials: "omit"`, `no-store`, `no-referrer` и `redirect: "error"`. Он не запускает auth refresh и CSRF.

```ts
const payload = await createTransactionRequest(
  createPublicTransactionRequestExecutor(),
  intent.action.uri,
  payerAddress,
);
```

Canonical URI хранится только в памяти: внутри короткоживущая capability. URI нельзя отправлять в логи, аналитику, browser history, storage или error reporting. Transaction-request endpoint обязан использовать HTTPS; `http://localhost`, `http://127.0.0.1` и `http://[::1]` разрешены только для локальной разработки.

## Точные суммы и граница проверки

Коммерческие decimal amounts и blockchain base units передаются строками. `parseDecimalAmount`, `parseRawAmount`, `decimalAmountToRaw` и `rawAmountToDecimal` не используют JavaScript `Number` для расчётов.

Runtime parsers связывают cluster с точным genesis hash, декодируют address/signature в 32/64 bytes, требуют positive u64 raw amounts и согласованные границы option, а также проверяют `display_amount == expected_raw_amount / 10^decimals`. В каждом immutable settlement обязателен canonical snapshot `recipient_policy_version` для audit provenance resolver-а; он не заменяет точный адрес получателя при verification.

`validateInspectedTransaction` принимает только один точный top-level перевод native SOL либо Token-2022 `TransferChecked` с ожидаемыми payer, canonical Token-2022 associated token account плательщика, mint, token account получателя, raw amount, decimals, reference, обязательным подписанным memo `orcestr-issuance:<uuid4>` и точным опциональным settlement memo. Встроенный inspector также требует строгий порядок: issuance memo первым, settlement memo вторым (если есть), платёжная инструкция последней. Это защита перед подписью; окончательное решение об оплате принимает backend после нужного commitment.

## Сборка

Для локальной интеграции сначала соберите source workspace, укажите в consumer точную зависимость `file:../../orcestr-commerce-solana/frontend/packages/core` и выполните его `npm install` до запуска dev server. Глобальный `npm link` не используйте; перед релизным коммитом верните registry-версию `0.1.0`.

Из папки `frontend` репозитория:

```bash
npm run typecheck
npm test
npm run build
npm pack --dry-run --workspace @orcestr/commerce-solana-core
```
