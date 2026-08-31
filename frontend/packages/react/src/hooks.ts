"use client";

import type {
  CancelSolanaPaymentIntentInput,
  CommercePayment,
  CreateCommercePaymentAttemptInput,
  CreateSolanaPaymentIntentInput,
  SolanaCheckoutAction,
  SolanaPaymentIntent,
} from "@orcestr/commerce-solana-core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useSolanaCommerceClient } from "./provider.js";
import { solanaCommerceQueryKeys } from "./queryKeys.js";

export function useSolanaPaymentOptions(
  orderPublicId: string | null,
  options: { readonly enabled?: boolean } = {},
) {
  const client = useSolanaCommerceClient();
  const enabled = (options.enabled ?? true) && orderPublicId !== null;
  return useQuery({
    queryKey: solanaCommerceQueryKeys.paymentOptions(orderPublicId ?? ""),
    queryFn: ({ signal }) =>
      client.listPaymentOptions(orderPublicId ?? "", signal),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    refetchInterval: false,
  });
}

/** CommerceXL 0.3.2 provider-neutral payment options with an order price snapshot. */
export function useCommercePaymentOptions(
  orderPublicId: string | null,
  options: { readonly enabled?: boolean } = {},
) {
  const client = useSolanaCommerceClient();
  const enabled = (options.enabled ?? true) && orderPublicId !== null;
  return useQuery({
    queryKey: solanaCommerceQueryKeys.commercePaymentOptions(
      orderPublicId ?? "",
    ),
    queryFn: ({ signal }) =>
      client.listCommercePaymentOptions(orderPublicId ?? "", signal),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    refetchInterval: false,
  });
}

export function useSolanaPaymentIntent(
  paymentPublicId: string | null,
  options: { readonly enabled?: boolean } = {},
) {
  const client = useSolanaCommerceClient();
  const enabled = (options.enabled ?? true) && paymentPublicId !== null;
  return useQuery({
    queryKey: solanaCommerceQueryKeys.intent(paymentPublicId ?? ""),
    queryFn: ({ signal }) =>
      client.getPaymentIntent(paymentPublicId ?? "", signal),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    refetchInterval: false,
  });
}

/** CommerceXL 0.3.2 canonical payment state. */
export function useCommercePayment(
  paymentPublicId: string | null,
  options: { readonly enabled?: boolean } = {},
) {
  const client = useSolanaCommerceClient();
  const enabled = (options.enabled ?? true) && paymentPublicId !== null;
  return useQuery({
    queryKey: solanaCommerceQueryKeys.commercePayment(paymentPublicId ?? ""),
    queryFn: ({ signal }) =>
      client.getCommercePayment(paymentPublicId ?? "", signal),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    refetchInterval: false,
  });
}

export function useCreateSolanaPaymentIntent() {
  const client = useSolanaCommerceClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateSolanaPaymentIntentInput) =>
      client.createPaymentIntent(input),
    onSuccess: (intent) => {
      cacheIntent(queryClient, intent);
      void queryClient.invalidateQueries({
        queryKey: solanaCommerceQueryKeys.paymentOptions(intent.orderPublicId),
      });
    },
  });
}

/** Creates a payment through the stable CommerceXL 0.3.2 attempt endpoint. */
export function useCreateCommercePaymentAttempt() {
  const client = useSolanaCommerceClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateCommercePaymentAttemptInput) =>
      client.createCommercePaymentAttempt(input),
    onSuccess: (payment) => {
      cacheCommercePayment(queryClient, payment);
      void queryClient.invalidateQueries({
        queryKey: solanaCommerceQueryKeys.commercePaymentOptions(
          payment.orderId,
        ),
      });
    },
  });
}

export function useSubmitSolanaCandidateSignature() {
  const client = useSolanaCommerceClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      paymentPublicId,
      signature,
    }: {
      readonly paymentPublicId: string;
      readonly signature: string;
    }) => client.submitCandidateSignature({ paymentPublicId, signature }),
    onSuccess: (intent) => cacheIntent(queryClient, intent),
  });
}

export function useCancelSolanaPaymentIntent() {
  const client = useSolanaCommerceClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CancelSolanaPaymentIntentInput) =>
      client.cancelPaymentIntent(input),
    onSuccess: (intent) => cacheIntent(queryClient, intent),
  });
}

/** Issues a fresh short-lived addon action without changing the intent. */
export function useIssueSolanaPaymentAction() {
  const client = useSolanaCommerceClient();
  const queryClient = useQueryClient();
  return useMutation({
    gcTime: 0,
    mutationFn: async (paymentPublicId: string) => {
      const action = await client.issuePaymentIntentAction(paymentPublicId);
      cacheIntentAction(queryClient, paymentPublicId, action);
    },
  });
}

/** Reissues the generic CommerceXL checkout action for a payment. */
export function useIssueCommerceCheckoutAction() {
  const client = useSolanaCommerceClient();
  const queryClient = useQueryClient();
  return useMutation({
    gcTime: 0,
    mutationFn: async (paymentPublicId: string) => {
      const action = await client.issueCommerceCheckoutAction(paymentPublicId);
      queryClient.setQueryData<CommercePayment>(
        solanaCommerceQueryKeys.commercePayment(paymentPublicId),
        (payment) => (payment ? { ...payment, action } : payment),
      );
    },
  });
}

function cacheIntent(
  queryClient: ReturnType<typeof useQueryClient>,
  intent: SolanaPaymentIntent,
): void {
  queryClient.setQueryData(
    solanaCommerceQueryKeys.intent(intent.paymentPublicId),
    intent,
  );
}

function cacheIntentAction(
  queryClient: ReturnType<typeof useQueryClient>,
  paymentPublicId: string,
  action: SolanaCheckoutAction,
): void {
  queryClient.setQueryData<SolanaPaymentIntent>(
    solanaCommerceQueryKeys.intent(paymentPublicId),
    (intent) => (intent ? { ...intent, action } : intent),
  );
}

function cacheCommercePayment(
  queryClient: ReturnType<typeof useQueryClient>,
  payment: CommercePayment,
): void {
  queryClient.setQueryData(
    solanaCommerceQueryKeys.commercePayment(payment.id),
    payment,
  );
}
