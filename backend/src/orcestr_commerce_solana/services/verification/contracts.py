from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from solders.signature import Signature
from uuid import UUID

from orcestr_commerce_solana.schemas.assets import SettlementSnapshot, SolanaCluster, SolanaCommitment
from orcestr_commerce_solana.schemas.transactions import TransactionIssuanceSnapshot, TransactionVersion


class VerificationDisposition(StrEnum):
    """Separates retryable uncertainty from verified financial outcomes."""

    UNKNOWN = "unknown"
    OBSERVED = "observed"
    CONFIRMED = "confirmed"
    MATCH = "match"
    REVIEW = "review"
    FAILED = "failed"


class VerificationRequest(BaseModel):
    """Combines persisted snapshots with one untrusted candidate signature."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signature: str = Field(min_length=64, max_length=100)
    intent_public_id: UUID
    settlement: SettlementSnapshot
    issuance: TransactionIssuanceSnapshot
    now: datetime
    detection_source: str = Field(default="candidate", pattern=r"^(candidate|reference_scan|manual)$")

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, value: str) -> str:
        """Rejects malformed candidate signatures before JSON-RPC calls."""
        try:
            parsed = Signature.from_string(value)
        except ValueError as exc:
            raise ValueError("Verification signature must be canonical base58.") from exc
        if str(parsed) != value:
            raise ValueError("Verification signature must be canonical base58.")
        return value


class VerifiedTransfer(BaseModel):
    """Normalizes the exact top-level movement accepted from raw transaction bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster: SolanaCluster
    signature: str
    issuance_public_id: UUID
    instruction_index: int = Field(ge=0)
    source_account: str
    destination_account: str
    mint: str | None = None
    token_program: str | None = None
    gross_raw_amount: str = Field(pattern=r"^[0-9]+$")
    net_raw_amount: str = Field(pattern=r"^[0-9]+$")
    slot: int = Field(ge=0)
    block_time: datetime | None = None
    commitment: SolanaCommitment
    transaction_version: TransactionVersion
    detection_source: str
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class VerificationAttemptEvidence(BaseModel):
    """Identifies one exact issued raw transaction that did not settle the intent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signature: str
    issuance_public_id: UUID
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class VerificationResult(BaseModel):
    """Returns a typed verdict and never executes the CommerceXL order itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: VerificationDisposition
    reason_code: str | None = None
    retryable: bool = False
    commitment: SolanaCommitment | None = None
    transfer: VerifiedTransfer | None = None
    attempt: VerificationAttemptEvidence | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> VerificationResult:
        """Requires normalized raw evidence for confirmed or final settlement."""
        if self.disposition in {VerificationDisposition.CONFIRMED, VerificationDisposition.MATCH} and self.transfer is None:
            raise ValueError("Confirmed and matching verdicts require verified transfer evidence.")
        if self.disposition == VerificationDisposition.UNKNOWN and not self.retryable:
            raise ValueError("UNKNOWN verdict must remain retryable.")
        if self.attempt is not None and self.disposition not in {
            VerificationDisposition.REVIEW,
            VerificationDisposition.FAILED,
        }:
            raise ValueError("Attempt evidence belongs only to REVIEW or FAILED verdicts.")
        if self.attempt is not None and self.transfer is not None:
            raise ValueError("Verification evidence must use either a transfer or an attempt record.")
        return self


class VerificationResultFactory:
    """Builds consistent verdicts used by the canonical verifier."""

    @staticmethod
    def unknown(reason_code: str) -> VerificationResult:
        """Returns uncertainty that must not mutate a payment to success or failure."""
        return VerificationResult(
            disposition=VerificationDisposition.UNKNOWN,
            reason_code=reason_code,
            retryable=True,
        )

    @staticmethod
    def review(reason_code: str) -> VerificationResult:
        """Returns a persisted mismatch requiring reconciliation."""
        return VerificationResult(
            disposition=VerificationDisposition.REVIEW,
            reason_code=reason_code,
        )

    @staticmethod
    def failed(reason_code: str) -> VerificationResult:
        """Returns an on-chain execution failure that cannot settle the order."""
        return VerificationResult(
            disposition=VerificationDisposition.FAILED,
            reason_code=reason_code,
        )
