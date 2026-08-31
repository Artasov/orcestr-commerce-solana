from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic import field_validator
from solders.signature import Signature

from orcestr_commerce_solana.clock import utc_datetime
from orcestr_commerce_solana.schemas.assets import SettlementSnapshot, SolanaCommitment


class SolanaIntentState(StrEnum):
    """Provider state shown to clients and reconciliation tools."""

    PREPARING = "preparing"
    WAITING = "waiting"
    OBSERVED = "observed"
    CONFIRMED = "confirmed"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"
    REVIEW = "review"


class SolanaActionKind(StrEnum):
    """Checkout transports implemented by frontend renderers."""

    TRANSACTION_REQUEST = "solana_transaction_request"


class SolanaCheckoutAction(BaseModel):
    """Returns a canonical action while keeping its capability out of storage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SolanaActionKind
    uri: str = Field(min_length=1, max_length=4000)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime) -> datetime:
        return utc_datetime(value, "Checkout action expiry")


class SolanaPaymentOptionDTO(BaseModel):
    """Describes a host-approved Solana settlement option for one order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    symbol: str
    decimals: int
    mint: str | None = None
    minimum_raw_amount: str
    maximum_raw_amount: str
    required_commitment: Literal[SolanaCommitment.FINALIZED]


class SolanaIntentDTO(BaseModel):
    """Public authenticated view of a payment intent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    public_id: UUID
    payment_public_id: UUID
    order_public_id: UUID
    state: SolanaIntentState
    settlement: SettlementSnapshot
    action: SolanaCheckoutAction | None = None
    candidate_signature: str | None = None
    verified_signature: str | None = None
    reason_code: str | None = None
    revision: int = Field(ge=0)
    expires_at: datetime
    created_at: datetime
    updated_at: datetime

    @field_validator("expires_at", "created_at", "updated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return utc_datetime(value, "Intent timestamp")


class SolanaIntentCreateRequest(BaseModel):
    """Selects only server-published order and option identities."""

    model_config = ConfigDict(extra="forbid")

    order_public_id: UUID
    payment_option_id: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=200)


class CandidateSignatureRequest(BaseModel):
    """Submits an untrusted acceleration hint from a connected wallet."""

    model_config = ConfigDict(extra="forbid")

    signature: str = Field(min_length=64, max_length=100)

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, value: str) -> str:
        """Rejects malformed or non-canonical base58 before persistence and RPC."""
        try:
            parsed = Signature.from_string(value)
        except ValueError as exc:
            raise ValueError("Candidate signature must be canonical base58.") from exc
        if str(parsed) != value:
            raise ValueError("Candidate signature must be canonical base58.")
        return value


class CancelIntentRequest(BaseModel):
    """Carries a user-visible cancellation reason for the audit trail."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="user_cancelled", min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=200)


class SolanaPaymentOptionsResponse(BaseModel):
    """Wraps available options under a stable typed field."""

    model_config = ConfigDict(extra="forbid")

    options: tuple[SolanaPaymentOptionDTO, ...]
