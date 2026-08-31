from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from solders.pubkey import Pubkey

from orcestr_commerce_solana.schemas.assets import SettlementSnapshot
from orcestr_commerce_solana.clock import utc_datetime


class TransactionVersion(StrEnum):
    """Transaction message versions emitted and verified by the package."""

    LEGACY = "legacy"
    V0 = "v0"


class TransactionIssuanceSnapshot(BaseModel):
    """Pins the exact unsigned message issued to one payer wallet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    public_id: UUID
    payer: str = Field(min_length=32, max_length=44)
    recent_blockhash: str = Field(min_length=32, max_length=44)
    last_valid_block_height: int = Field(ge=0)
    issued_context_slot: int = Field(ge=0)
    message_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: TransactionVersion = TransactionVersion.V0
    issued_at: datetime
    accepts_until: datetime

    @field_validator("issued_at", "accepts_until")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return utc_datetime(value, "Issuance timestamp")

    @model_validator(mode="after")
    def validate_window(self) -> TransactionIssuanceSnapshot:
        if self.issued_at >= self.accepts_until:
            raise ValueError("Issuance acceptance window must end after issuance.")
        return self


class UnsignedTransactionDTO(BaseModel):
    """Returns a base64 transaction and facts the host must persist as issuance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transaction: str
    message: str = Field(min_length=1, max_length=500)
    issuance: TransactionIssuanceSnapshot


class TransactionBuildRequest(BaseModel):
    """Builds a minimal transaction from persisted settlement facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issuance_public_id: UUID
    payer: str = Field(min_length=32, max_length=44)
    settlement: SettlementSnapshot
    issued_at: datetime
    accepts_until: datetime

    @field_validator("issued_at", "accepts_until")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return utc_datetime(value, "Transaction build timestamp")


class SolanaPayGetResponse(BaseModel):
    """Implements the public Solana Pay transaction-request GET response."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=100)
    icon: str | None = Field(default=None, max_length=2048)

    @field_validator("icon")
    @classmethod
    def validate_icon(cls, value: str | None) -> str | None:
        """Allows only absolute HTTP(S) merchant-controlled image URLs."""
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Solana Pay icon must be an absolute HTTP(S) URL without credentials.")
        return value


class SolanaPayPostRequest(BaseModel):
    """Accepts the payer account required by the Solana Pay protocol."""

    model_config = ConfigDict(extra="ignore")

    account: str = Field(min_length=32, max_length=44)

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str) -> str:
        """Validates the payer public key before transaction construction."""
        try:
            parsed = Pubkey.from_string(value)
        except ValueError as exc:
            raise ValueError("Payer account must be a canonical Solana public key.") from exc
        if str(parsed) != value:
            raise ValueError("Payer account must be a canonical Solana public key.")
        return value


class SolanaPayPostResponse(BaseModel):
    """Returns the unsigned base64 transaction for wallet review and signing."""

    model_config = ConfigDict(extra="forbid")

    transaction: str
    message: str = Field(min_length=1, max_length=500)
