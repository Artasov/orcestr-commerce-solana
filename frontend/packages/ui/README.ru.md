# @orcestr/commerce-solana-ui

Доступные адаптивные компоненты Orcestr UI для оплаты native SOL и Token-2022 через transaction request. Версия 0.1 не отображает статический transfer request и legacy Token Program.

## Установка

```bash
npm install @orcestr/commerce-solana-ui @orcestr/ui react
```

UI-only пакет не устанавливает wallet/query runtime. Для примера с подключённым кошельком добавьте `@orcestr/commerce-solana-react` и `@tanstack/react-query`.

Один раз подключите оба файла стилей:

```ts
import "@orcestr/ui/styles.css";
import "@orcestr/commerce-solana-ui/styles.css";
```

Компоненты работают внутри существующих providers Orcestr UI. Общий toast runtime используется для `CopyButton`.

```tsx
function PaymentDialog() {
  const issueAction = useIssueSolanaPaymentAction();
  return (
    <SolanaCommerceI18nProvider locale="ru">
      <SolanaCheckoutDialog
        open={open}
        onOpenChange={setOpen}
        intent={intent}
        walletConnected={Boolean(account)}
        submittingWalletPayment={submit.isPending}
        onPayWithWallet={() => submit.mutate({ wallet, account })}
        requestingAction={issueAction.isPending}
        onRequestAction={() => issueAction.mutate(intent.paymentPublicId)}
        onCancel={cancel}
        onRetry={createNewAttempt}
      />
    </SolanaCommerceI18nProvider>
  );
}
```

`SolanaCheckoutDialog` не размонтируется при закрытии и отдаёт exit-анимацию компоненту Dialog из `@orcestr/ui`. Управляемый `SolanaCheckout` можно встроить прямо в карточку заказа.

Стандартный formatter срока действия выводит детерминированный UTC ISO timestamp, поэтому server render не создаёт locale/time-zone hydration mismatch. Browser-only host может передать `formatExpiresAt` для локальной даты и времени пользователя.

## Компоненты

- `SolanaCheckout` и `SolanaCheckoutDialog`
- `SolanaPaymentState`
- `SolanaQrCode`
- `SolanaAssetSelector`
- `SolanaWalletSelector`
- `SolanaCommerceI18nProvider`
- `solanaPaymentRendererDescriptor`

Checkout различает preparing, waiting, observed, confirmed, paid, expired, cancelled, failed и review. Состояние `paid` появляется только после finalized-проверки и атомарного применения продукта. Он показывает сеть, точную сумму, Token-2022 mint, получателя и expiry текущего action. Некорректная или истёкшая capability обрабатывается fail-closed: QR, deep link и копирование исчезают, а `onRequestAction` может выпустить новую короткоживущую ссылку без дублирования платежа. Локальная отправка из кошелька сама по себе никогда не переводит UI в `paid`.

QR создаётся локально библиотекой `qrcode` как image data URL. Удалённый QR-сервис и исполнение сторонних изображений не используются. Canonical URI с capability живёт только в памяти компонента; host исключает его из telemetry, логов, storage и error reporting.

`SolanaWalletSelector` отображает только ограниченные по размеру `data:image/*` иконки кошельков и игнорирует remote URL, поэтому иконка не превращается в tracking request.

Русский и английский каталоги полные и типизированные. Продуктовые формулировки можно заменить section overrides в `SolanaCommerceI18nProvider`.

Глобальный provider может импортировать `SolanaCommerceI18nProvider` из `@orcestr/commerce-solana-ui/i18n`, чтобы checkout и локальный QR generator не попадали в initial bundle до первого использования.

## Сборка

Для локальной интеграции сначала соберите source workspace, затем укажите в consumer-е `file:../../orcestr-commerce-solana/frontend/packages/core`, `file:../../orcestr-commerce-solana/frontend/packages/react` и `file:../../orcestr-commerce-solana/frontend/packages/ui`. Выполните `npm install` consumer-а до запуска dev server; глобальный `npm link` не используйте. Перед релизным коммитом верните registry-версии `0.2.0`.

```bash
npm run typecheck
npm test
npm run build
npm pack --dry-run --workspace @orcestr/commerce-solana-ui
```
