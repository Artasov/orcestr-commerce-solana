export const solanaCommerceQueryKeys = {
  all: ["commerce", "solana"] as const,
  commercePaymentOptions: (orderPublicId: string) =>
    ["commerce", "solana", "commerce-payment-options", orderPublicId] as const,
  commercePayment: (paymentPublicId: string) =>
    ["commerce", "solana", "commerce-payment", paymentPublicId] as const,
  paymentOptions: (orderPublicId: string) =>
    ["commerce", "solana", "payment-options", orderPublicId] as const,
  intent: (paymentPublicId: string) =>
    ["commerce", "solana", "payment-intent", paymentPublicId] as const,
};
