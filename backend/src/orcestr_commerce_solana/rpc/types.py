from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from solders.hash import Hash
from solders.signature import Signature

from orcestr_commerce_solana.clock import utc_datetime
from orcestr_commerce_solana.constants import MAX_SOLANA_TRANSACTION_BYTES


class RpcAccountInfo(BaseModel):
    """Normalizes the base64 account fields used by asset activation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner: str
    lamports: int = Field(ge=0)
    executable: bool
    rent_epoch: int = Field(ge=0)
    data: bytes


class RpcLatestBlockhash(BaseModel):
    """Carries a recent blockhash and its exact validity boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    blockhash: str
    last_valid_block_height: int = Field(ge=0)
    context_slot: int = Field(ge=0)

    @field_validator("blockhash")
    @classmethod
    def validate_blockhash(cls, value: str) -> str:
        if str(Hash.from_string(value)) != value:
            raise ValueError("RPC blockhash must be canonical base58.")
        return value


class RpcSignatureStatus(BaseModel):
    """Normalizes one signature status without trusting parsed transactions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: int = Field(ge=0)
    confirmations: int | None = Field(default=None, ge=0)
    confirmation_status: str | None = None
    error: Any = None


class RpcSignatureInfo(BaseModel):
    """Represents one history entry returned for a unique reference account."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signature: str
    slot: int = Field(ge=0)
    block_time: datetime | None = None
    error: Any = None
    memo: str | None = None

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, value: str) -> str:
        if str(Signature.from_string(value)) != value:
            raise ValueError("RPC signature must be canonical base58.")
        return value

    @field_validator("block_time")
    @classmethod
    def validate_block_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else utc_datetime(value, "RPC block time")


class RpcTransaction(BaseModel):
    """Keeps raw transaction bytes plus the metadata needed by the verifier."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    slot: int = Field(ge=0)
    block_time: datetime | None = None
    raw_transaction: bytes = Field(min_length=1, max_length=MAX_SOLANA_TRANSACTION_BYTES)
    meta: dict[str, Any]

    @field_validator("block_time")
    @classmethod
    def validate_block_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else utc_datetime(value, "RPC transaction block time")
