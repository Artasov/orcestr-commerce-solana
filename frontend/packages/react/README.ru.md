# @orcestr/commerce-solana-react

Интеграция Orcestr Commerce Solana с React 19, TanStack Query 5 и Wallet Standard.

## Установка

```bash
npm install @orcestr/commerce-solana-react @tanstack/react-query react
```

Пакет работает внутри host `QueryClientProvider`. Он не создаёт второй QueryClient, auth manager, WebSocket или polling.

```tsx
const eventSource: PaymentEventSource = {
  subscribe(listener) {
    return sharedSocket.addListener("commerce.payment.updated", listener);
  },
  subscribeReconnect(listener) {
    return sharedSocket.addReconnectListener(listener);
  },
};

<QueryClientProvider client={queryClient}>
  <SolanaCommerceProvider
    client={solanaClient}
    eventSource={eventSource}
    onPaymentEvent={() => {
      void queryClient.invalidateQueries({
        queryKey: ["subscription-overview"],
      });
      void queryClient.invalidateQueries({ queryKey: ["credit-account"] });
      void queryClient.invalidateQueries({ queryKey: ["credit-ledger"] });
    }}
  >
    {children}
  </SolanaCommerceProvider>
</QueryClientProvider>;
```

Payload общего события: `{ event: "commerce.payment.updated", order_public_id, payment_public_id, revision }`. Первая доставка revision через socket инвалидирует authoritative REST queries и вызывает `onPaymentEvent`, даже если candidate response уже положил эту revision в cache. Поэтому host может тем же committed event обновить entitlement/credit queries. Повторные socket revision игнорируются. После reconnect один раз инвалидируются активные Solana queries; `refetchInterval` отсутствует.

## Hooks

- `useCommercePaymentOptions`
- `useCreateCommercePaymentAttempt`
- `useCommercePayment`
- `useIssueCommerceCheckoutAction`
- `useSolanaPaymentOptions`
- `useCreateSolanaPaymentIntent`
- `useSolanaPaymentIntent`
- `useIssueSolanaPaymentAction`
- `useSubmitSolanaCandidateSignature`
- `useCancelSolanaPaymentIntent`
- `useWalletStandardConnection`
- `useSubmitWalletPayment`

Для отмены caller передаёт idempotency key и audit reason.

CommerceXL hooks образуют обычный flow карточки заказа. Детальный settlement загружается через `useSolanaPaymentIntent(payment.id)`. Если активный intent открыт без action, `useIssueSolanaPaymentAction()` выпускает новую ссылку, сохраняя intent и reference.

## Подключённый кошелёк

`useWalletStandardConnection` показывает только зарегистрированные кошельки со стандартными connect и Solana sign-and-send features. До получения unsigned transaction `useSubmitWalletPayment` отклоняет истёкший action, intent вне waiting/preparing, account другой сети и кошелёк без v0. Затем transaction проверяется, кошелёк подписывает и отправляет её, а signature передаётся backend-у только как недоверенная подсказка для ускорения поиска.

```tsx
const payment = useSubmitWalletPayment({
  publicExecutor: createPublicTransactionRequestExecutor(),
  intent,
});

payment.mutate({ wallet, account });
```

Встроенный `kitTransactionInspector` использует точную версию `@solana/kit` 8.2.0. Он принимает минимальную v0 transaction backend-а без address lookup tables: обязательный подписанный memo `orcestr-issuance:<uuid4>` первым, точный опциональный settlement memo вторым и ровно один последний native transfer либо Token-2022 `TransferChecked`. Connected-wallet adapter выводит canonical Token-2022 associated token account плательщика и сравнивает его с inspected source. Неизвестная программа, non-associated source, отсутствующие/лишние/переставленные memo, несколько переводов, изменённые amount/mint/destination/reference, лишний signer и неизвестная структура отклоняются до открытия wallet prompt. Host может передать другой `SolanaTransactionInspector`, но для стандартной интеграции копировать decoder не требуется.

Wallet mutation сохраняет authoritative response в intent query и не возвращает mutation data с capability. Action-reissue mutations работают так же и имеют нулевое удержание в mutation cache после reset либо unmount observer-а. При закрытии долгоживущего диалога сбрасывайте mutation и исключайте intent/payment queries из persisted-query и telemetry: action может содержать short-lived capability URI.

Callback кошелька не считается доказательством оплаты. Продукт можно выдавать только после backend state `paid`.

Если важны bundle boundaries, импортируйте subpaths `provider`, `query-keys`, `hooks`, `wallet` и `kit-inspector`. Тогда глобальный provider не загрузит Wallet Standard и decoder Solana Kit до открытия wallet checkout; корень пакета остаётся convenience barrel.

## Сборка

Для локальной интеграции сначала соберите source workspace, затем укажите в consumer точные зависимости `file:../../orcestr-commerce-solana/frontend/packages/core` и `file:../../orcestr-commerce-solana/frontend/packages/react`. Выполните `npm install` consumer-а до запуска dev server; глобальный `npm link` не используйте. Перед релизным коммитом верните registry-версии `0.2.0`.

```bash
npm run typecheck
npm test
npm run build
npm pack --dry-run --workspace @orcestr/commerce-solana-react
```
