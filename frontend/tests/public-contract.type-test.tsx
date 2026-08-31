import {
  SolanaCommerceClient,
  createAuthenticatedFetchExecutor,
  createPublicTransactionRequestExecutor,
  parseRawAmount,
  type SolanaTransactionInspector,
} from "@orcestr/commerce-solana-core";
import {
  SolanaCommerceProvider,
  useCommercePayment,
  useCommercePaymentOptions,
  useCreateCommercePaymentAttempt,
  useIssueSolanaPaymentAction,
  useSolanaPaymentIntent,
  useSubmitWalletPayment,
} from "@orcestr/commerce-solana-react";
import {
  SolanaCheckout,
  SolanaCommerceI18nProvider,
} from "@orcestr/commerce-solana-ui";
import type { ReactNode } from "react";

declare const authFetch: typeof fetch;

const client = new SolanaCommerceClient({
  baseUrl: "https://app.example.com/api/",
  executor: createAuthenticatedFetchExecutor(authFetch),
});

const inspector: SolanaTransactionInspector = () => ({
  transactionVersion: 0,
  feePayer: "HztMC7xr2j6ngpfWbYsF5xnRZkgouDXQ5rVis3C3pump",
  signerAddresses: ["HztMC7xr2j6ngpfWbYsF5xnRZkgouDXQ5rVis3C3pump"],
  referenceAddresses: ["SysvarRent111111111111111111111111111111111"],
  issuanceMemo: "orcestr-issuance:00000000-0000-4000-8000-000000000004",
  memo: null,
  transfers: [
    {
      instructionLevel: "top_level",
      assetKind: "token",
      tokenProgram: "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
      mint: "HztMC7xr2j6ngpfWbYsF5xnRZkgouDXQ5rVis3C3pump",
      sourceTokenAccount: "11111111111111111111111111111111",
      recipientAddress: null,
      recipientTokenAccount: "11111111111111111111111111111111",
      rawAmount: parseRawAmount("1"),
      decimals: 6,
    },
  ],
});

function Host({ children }: { readonly children: ReactNode }) {
  return (
    <SolanaCommerceProvider
      client={client}
      onPaymentEvent={(event) => void event.revision}
    >
      <SolanaCommerceI18nProvider locale="ru">
        {children}
      </SolanaCommerceI18nProvider>
    </SolanaCommerceProvider>
  );
}

function Checkout() {
  const query = useSolanaPaymentIntent("pay_01");
  const commerceOptions = useCommercePaymentOptions(null);
  const commercePayment = useCommercePayment(null);
  const createAttempt = useCreateCommercePaymentAttempt();
  const issueAction = useIssueSolanaPaymentAction();
  const submit = useSubmitWalletPayment({
    publicExecutor: createPublicTransactionRequestExecutor(),
    intent: query.data ?? null,
  });
  void commerceOptions;
  void commercePayment;
  void createAttempt;
  void issueAction;
  return query.data ? (
    <SolanaCheckout
      intent={query.data}
      submittingWalletPayment={submit.isPending}
    />
  ) : null;
}

void Host;
void Checkout;
void inspector;
