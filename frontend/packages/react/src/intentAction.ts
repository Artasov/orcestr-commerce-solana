import {isSolanaCheckoutActionActive, type SolanaPaymentIntent, type SolanaSettlementSnapshot} from "@orcestr/commerce-solana-core";

/** Status reads omit capability URLs; keep a live local action only for the same immutable payment. */
export function mergeIntentAction(next: SolanaPaymentIntent, previous: SolanaPaymentIntent | undefined, now = Date.now()): SolanaPaymentIntent {
  if (next.action !== null || !previous?.action ||
      !["preparing", "waiting"].includes(next.state) ||
      !["preparing", "waiting"].includes(previous.state) ||
      next.publicId !== previous.publicId || next.paymentPublicId !== previous.paymentPublicId ||
      next.orderPublicId !== previous.orderPublicId || next.revision < previous.revision ||
      !sameSettlement(next.settlement, previous.settlement) ||
      !isSolanaCheckoutActionActive(previous.action, now) || Date.parse(next.expiresAt) <= now) return next;
  return {...next, action: previous.action};
}

function sameSettlement(next: SolanaSettlementSnapshot, previous: SolanaSettlementSnapshot): boolean {
  const {quote: nextQuote, ...nextFields} = next;
  const {quote: previousQuote, ...previousFields} = previous;
  return sameFields(nextFields, previousFields) && sameFields(nextQuote, previousQuote);
}

function sameFields<T extends object>(left: T, right: T): boolean {
  const keys = Object.keys(left) as (keyof T)[];
  return keys.length === Object.keys(right).length && keys.every(key => left[key] === right[key]);
}
