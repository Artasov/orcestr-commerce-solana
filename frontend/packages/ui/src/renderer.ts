import {
  isSolanaCheckoutAction,
  type SolanaCheckoutAction,
} from "@orcestr/commerce-solana-core";

export type SolanaPaymentRendererDescriptor = {
  readonly id: "orcestr-commerce-solana";
  readonly paymentSystem: "solana";
  readonly supportsAction: (action: unknown) => action is SolanaCheckoutAction;
};

export const solanaPaymentRendererDescriptor: SolanaPaymentRendererDescriptor = {
  id: "orcestr-commerce-solana",
  paymentSystem: "solana",
  supportsAction: isSolanaCheckoutAction,
};
