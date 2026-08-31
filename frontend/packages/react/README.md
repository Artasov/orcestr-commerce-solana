# @orcestr/commerce-solana-react

React 19, TanStack Query 5, and Wallet Standard integration for Orcestr Commerce Solana.

## Install

```bash
npm install @orcestr/commerce-solana-react @tanstack/react-query react
```

The package uses the host `QueryClientProvider`. It never creates another query client, authentication manager, WebSocket, or polling loop.

```tsx
import { QueryClientProvider } from "@tanstack/react-query";
import {
  SolanaCommerceProvider,
  type PaymentEventSource,
} from "@orcestr/commerce-solana-react/provider";

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

The shared event payload is `{ event: "commerce.payment.updated", order_public_id, payment_public_id, revision }`. The first socket delivery for a revision invalidates the authoritative REST queries and calls `onPaymentEvent` even if a candidate response already cached that revision, so a host can invalidate entitlement/credit queries on the same committed event. Replayed socket revisions are ignored. A reconnect invalidates active Solana queries once; there is no `refetchInterval`.

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

Cancellation requires a caller-owned idempotency key and audit reason.

The CommerceXL hooks are the normal order-card flow. Use `useSolanaPaymentIntent(payment.id)` for the detailed settlement and `useIssueSolanaPaymentAction()` when an active intent is reopened without an action. Action reissue preserves the intent and reference.

## Connected wallet

`useWalletStandardConnection` exposes only registered wallets with the standard connect and Solana sign-and-send features. `useSubmitWalletPayment` rejects an expired action, a non-waiting intent, a wrong-chain account, or a wallet without v0 support before requesting a short-lived unsigned transaction. It inspects the transaction, asks the wallet to sign/send, then submits the returned signature only as an untrusted acceleration hint.

```tsx
const publicExecutor = createPublicTransactionRequestExecutor();
const payment = useSubmitWalletPayment({ publicExecutor, intent });

payment.mutate({ wallet, account });
```

The default `kitTransactionInspector` is implemented with exact `@solana/kit` 8.2.0. It accepts the backend's minimal v0 transaction without address lookup tables: a required signed `orcestr-issuance:<uuid4>` memo first, the exact optional settlement memo second, and exactly one final native transfer or Token-2022 `TransferChecked`. The connected-wallet adapter derives the payer's canonical Token-2022 associated token account and matches it to the inspected source. Unsupported programs, non-associated sources, missing/extra/reordered memos, multiple transfers, altered amount/mint/destination/reference, extra signers, and unknown transaction shapes fail before the wallet prompt. Hosts may inject another `SolanaTransactionInspector`, but do not need to implement or copy a decoder for the standard integration.

The wallet mutation stores the authoritative response in the intent query and returns no capability-bearing mutation data. Action-reissue mutations follow the same rule and use zero mutation-cache retention after their observer resets or unmounts. Reset the mutation when a long-lived dialog closes, and exclude intent/payment queries from any persisted-query or telemetry integration because their action may contain a short-lived capability URI.

The browser callback is never treated as proof of payment. Only backend `paid` state may unlock a product.

Use the `provider`, `query-keys`, `hooks`, `wallet`, and `kit-inspector` subpath exports when bundle boundaries matter. This lets a global provider avoid loading the Wallet Standard and Solana Kit decoder until a wallet checkout is opened; the package root remains a convenience barrel.

## Build

For local integration, build the source workspace first and set the consumer's `@orcestr/commerce-solana-core` and `@orcestr/commerce-solana-react` dependencies to their exact `file:../../orcestr-commerce-solana/frontend/packages/core` and `.../react` paths. Run the consumer's `npm install` before its dev server; do not use a global `npm link`. Restore `0.1.0` registry versions before a release commit.

```bash
npm run typecheck
npm test
npm run build
npm pack --dry-run --workspace @orcestr/commerce-solana-react
```
