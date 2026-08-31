"use client";

import {
  parseSolanaPaymentUpdatedEvent,
  type SolanaCommerceClient,
  type SolanaPaymentUpdatedEvent,
} from "@orcestr/commerce-solana-core";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from "react";

import { solanaCommerceQueryKeys } from "./queryKeys.js";

export type PaymentEventSource = {
  readonly subscribe: (
    listener: (event: unknown) => void,
  ) => () => void;
  readonly subscribeReconnect?: (listener: () => void) => () => void;
};

export type SolanaCommerceProviderProps = {
  readonly client: SolanaCommerceClient;
  readonly eventSource?: PaymentEventSource | null;
  readonly onPaymentEvent?: (event: SolanaPaymentUpdatedEvent) => void;
  readonly onEventError?: (error: unknown) => void;
  readonly children: ReactNode;
};

type SolanaCommerceContextValue = {
  readonly client: SolanaCommerceClient;
};

const SolanaCommerceContext = createContext<SolanaCommerceContextValue | null>(
  null,
);

export function SolanaCommerceProvider({
  client,
  eventSource = null,
  onPaymentEvent,
  onEventError,
  children,
}: SolanaCommerceProviderProps) {
  const queryClient = useQueryClient();
  const value = useMemo(() => ({ client }), [client]);

  useEffect(() => {
    if (!eventSource) return undefined;
    return subscribeToSolanaPaymentEvents(
      queryClient,
      eventSource,
      onPaymentEvent,
      onEventError,
    );
  }, [eventSource, onEventError, onPaymentEvent, queryClient]);

  return (
    <SolanaCommerceContext.Provider value={value}>
      {children}
    </SolanaCommerceContext.Provider>
  );
}

export function useSolanaCommerceClient(): SolanaCommerceClient {
  const context = useContext(SolanaCommerceContext);
  if (!context) {
    throw new Error(
      "useSolanaCommerceClient must be used inside SolanaCommerceProvider.",
    );
  }
  return context.client;
}

/** Bridges the host socket to React Query without owning a socket or timer. */
export function subscribeToSolanaPaymentEvents(
  queryClient: QueryClient,
  eventSource: PaymentEventSource,
  onPaymentEvent?: (event: SolanaPaymentUpdatedEvent) => void,
  onEventError?: (error: unknown) => void,
): () => void {
  const lastEventRevisions = new Map<string, number>();
  const unsubscribe = eventSource.subscribe((rawEvent) => {
    try {
      const event = parseSolanaPaymentUpdatedEvent(rawEvent);
      const lastEventRevision = lastEventRevisions.get(event.paymentPublicId);
      if (
        lastEventRevision !== undefined &&
        event.revision <= lastEventRevision
      ) {
        return;
      }
      lastEventRevisions.set(event.paymentPublicId, event.revision);
      invalidatePaymentEvent(queryClient, event);
      onPaymentEvent?.(event);
    } catch (error) {
      onEventError?.(error);
    }
  });
  const unsubscribeReconnect = eventSource.subscribeReconnect?.(() => {
    void queryClient.invalidateQueries({
      queryKey: solanaCommerceQueryKeys.all,
      refetchType: "active",
    });
  });
  return () => {
    unsubscribe();
    unsubscribeReconnect?.();
  };
}

function invalidatePaymentEvent(
  queryClient: QueryClient,
  event: SolanaPaymentUpdatedEvent,
): void {
  const intentKey = solanaCommerceQueryKeys.intent(event.paymentPublicId);
  const commercePaymentKey = solanaCommerceQueryKeys.commercePayment(
    event.paymentPublicId,
  );
  void queryClient.invalidateQueries({
    queryKey: intentKey,
  });
  void queryClient.invalidateQueries({
    queryKey: commercePaymentKey,
  });
  void queryClient.invalidateQueries({
    queryKey: solanaCommerceQueryKeys.paymentOptions(event.orderPublicId),
  });
  void queryClient.invalidateQueries({
    queryKey: solanaCommerceQueryKeys.commercePaymentOptions(
      event.orderPublicId,
    ),
  });
}
