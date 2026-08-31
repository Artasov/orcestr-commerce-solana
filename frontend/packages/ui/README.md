# @orcestr/commerce-solana-ui

Accessible, responsive Orcestr UI components for native SOL and Token-2022 transaction-request checkout. Version 0.1 does not render static transfer requests or the legacy Token Program.

## Install

```bash
npm install @orcestr/commerce-solana-ui @orcestr/ui react
```

The UI-only package does not install the wallet or query runtime. Add `@orcestr/commerce-solana-react` and `@tanstack/react-query` for the connected-wallet example below.

Import both stylesheets once in the host application:

```ts
import "@orcestr/ui/styles.css";
import "@orcestr/commerce-solana-ui/styles.css";
```

Render under the existing Orcestr UI providers. `CopyButton` uses the shared toast runtime.

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

`SolanaCheckoutDialog` remains mounted and delegates close animation to `@orcestr/ui` Dialog. The controlled `SolanaCheckout` can also be embedded directly in an order card.

The default expiry formatter is a deterministic UTC ISO timestamp, so server rendering cannot produce a locale/time-zone hydration mismatch. Browser-only hosts may pass `formatExpiresAt` for the user's local date and time.

## Components

- `SolanaCheckout` and `SolanaCheckoutDialog`
- `SolanaPaymentState`
- `SolanaQrCode`
- `SolanaAssetSelector`
- `SolanaWalletSelector`
- `SolanaCommerceI18nProvider`
- `solanaPaymentRendererDescriptor`

The checkout visibly separates preparing, waiting, observed, confirmed, paid, expired, cancelled, failed, and review states. `paid` is emitted only after finalized verification and atomic product application. It shows cluster, exact display amount, Token-2022 mint, recipient, and the current action expiry. Invalid or expired capabilities fail closed: their QR, deep link, and copy controls disappear and `onRequestAction` can request a fresh short-lived link without creating a duplicate payment. A wallet submission never changes the UI to paid by itself.

QR codes are generated locally with `qrcode` into an image data URL; no remote QR service or third-party image execution is used. The canonical capability URI stays in component memory. The host must exclude it from telemetry, logs, storage, and error reporting.

`SolanaWalletSelector` renders only bounded `data:image/*` wallet icons and ignores remote icon URLs, preventing an icon from becoming a tracking request.

English and Russian catalogs are complete and typed. Pass section overrides to `SolanaCommerceI18nProvider` for product wording.

Global providers can import `SolanaCommerceI18nProvider` from `@orcestr/commerce-solana-ui/i18n`, so the checkout and local QR generator remain outside the initial application bundle until needed.

## Build

For local integration, build the source workspace first and point the consumer to `file:../../orcestr-commerce-solana/frontend/packages/core`, `file:../../orcestr-commerce-solana/frontend/packages/react`, and `file:../../orcestr-commerce-solana/frontend/packages/ui`. Run the consumer's `npm install` before its dev server; do not use a global `npm link`. Restore `0.1.0` registry versions before a release commit.

```bash
npm run typecheck
npm test
npm run build
npm pack --dry-run --workspace @orcestr/commerce-solana-ui
```
