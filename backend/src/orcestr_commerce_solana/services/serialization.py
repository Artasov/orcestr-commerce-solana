from __future__ import annotations

from commercexl import PaymentState

from orcestr_commerce_solana.clock import database_utc_datetime
from orcestr_commerce_solana.models import SolanaPaymentIntentORM
from orcestr_commerce_solana.schemas.assets import SettlementSnapshot
from orcestr_commerce_solana.schemas.intents import (
    SolanaActionKind,
    SolanaCheckoutAction,
    SolanaIntentDTO,
    SolanaIntentState,
)


class SolanaIntentSerializer:
    """Combines provider snapshot with canonical CommerceXL payment state."""

    state_map = {
        PaymentState.CREATED: SolanaIntentState.PREPARING,
        PaymentState.REQUIRES_ACTION: SolanaIntentState.WAITING,
        PaymentState.PROCESSING: SolanaIntentState.OBSERVED,
        PaymentState.OBSERVED: SolanaIntentState.OBSERVED,
        PaymentState.CONFIRMED: SolanaIntentState.CONFIRMED,
        PaymentState.PAID: SolanaIntentState.PAID,
        PaymentState.EXPIRED: SolanaIntentState.EXPIRED,
        PaymentState.CANCELLED: SolanaIntentState.CANCELLED,
        PaymentState.FAILED: SolanaIntentState.FAILED,
        PaymentState.REVIEW: SolanaIntentState.REVIEW,
        PaymentState.REFUND_PENDING: SolanaIntentState.REVIEW,
        PaymentState.REFUNDED: SolanaIntentState.REVIEW,
    }

    @classmethod
    def serialize(cls, intent: SolanaPaymentIntentORM, payment: object) -> SolanaIntentDTO:
        """Returns the authenticated API representation without internal DB IDs."""
        action = None
        if payment.action is not None:
            if payment.action.uri is None or payment.action.expires_at is None:
                raise TypeError("Solana checkout action requires URI and expiry.")
            action = SolanaCheckoutAction(
                kind=SolanaActionKind(payment.action.kind),
                uri=payment.action.uri,
                expires_at=payment.action.expires_at,
            )
        return SolanaIntentDTO(
            public_id=intent.public_id,
            payment_public_id=payment.id,
            order_public_id=payment.order_id,
            state=cls.state_map[payment.state],
            settlement=SettlementSnapshot.model_validate(intent.settlement_snapshot),
            action=action,
            candidate_signature=intent.candidate_signature,
            verified_signature=intent.verified_signature,
            reason_code=payment.reason_code or intent.reason_code,
            revision=payment.revision,
            expires_at=database_utc_datetime(intent.expires_at),
            created_at=database_utc_datetime(intent.created_at),
            updated_at=max(
                database_utc_datetime(payment.updated_at),
                database_utc_datetime(intent.updated_at),
            ),
        )
