import {isSolanaCheckoutActionActive, type SolanaPaymentIntent} from "@orcestr/commerce-solana-core";

/** Status reads omit capability URLs; keep a live local action only for the same immutable payment. */
export function mergeIntentAction(next: SolanaPaymentIntent, previous: SolanaPaymentIntent | undefined, now = Date.now()): SolanaPaymentIntent {
  if (next.action !== null || !previous?.action ||
      !["preparing", "waiting"].includes(next.state) ||
      !["preparing", "waiting"].includes(previous.state) ||
      next.publicId !== previous.publicId || next.paymentPublicId !== previous.paymentPublicId ||
      next.orderPublicId !== previous.orderPublicId || next.revision < previous.revision ||
      JSON.stringify(next.settlement) !== JSON.stringify(previous.settlement) ||
      !isSolanaCheckoutActionActive(previous.action, now) || Date.parse(next.expiresAt) <= now) return next;
  return {...next, action: previous.action};
}
