"use client";

import type {
  KnownSolanaPaymentReasonCode,
  SolanaPaymentReasonCode,
  SolanaPaymentStatus,
} from "@orcestr/commerce-solana-core";
import { Spinner, StateCard, type StateCardTone } from "@orcestr/ui";

import { useSolanaCommerceMessages } from "./i18n.js";

export type SolanaPaymentStateProps = {
  readonly status: SolanaPaymentStatus;
  readonly reasonCode?: SolanaPaymentReasonCode | null;
  readonly onRetry?: () => void;
  readonly retryLabel?: string;
  readonly className?: string;
};

const STATUS_TONES: Record<SolanaPaymentStatus, StateCardTone> = {
  preparing: "info",
  waiting: "info",
  observed: "info",
  confirmed: "warning",
  paid: "success",
  expired: "warning",
  cancelled: "neutral",
  failed: "danger",
  review: "warning",
};

const BUSY_STATUSES = new Set<SolanaPaymentStatus>([
  "preparing",
  "waiting",
  "observed",
  "confirmed",
]);

export function SolanaPaymentState({
  status,
  reasonCode = null,
  onRetry,
  retryLabel,
  className,
}: SolanaPaymentStateProps) {
  const messages = useSolanaCommerceMessages();
  const reason = reasonCode
    ? Object.hasOwn(messages.reason, reasonCode)
      ? messages.reason[reasonCode as KnownSolanaPaymentReasonCode]
      : messages.checkout.unknownReason
    : null;
  return (
    <div aria-live="polite" aria-atomic="true">
      <StateCard
        className={className}
        tone={STATUS_TONES[status]}
        icon={BUSY_STATUSES.has(status) ? <Spinner size={2} /> : undefined}
        title={messages.status[status]}
        description={reason ?? messages.statusDescription[status]}
        action={
          onRetry ? (
            <button className="ocs-state-retry" type="button" onClick={onRetry}>
              {retryLabel ?? messages.actions.retry}
            </button>
          ) : undefined
        }
      />
    </div>
  );
}
