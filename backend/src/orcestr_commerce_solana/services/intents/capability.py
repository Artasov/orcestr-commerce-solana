from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime
from urllib.parse import quote
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from orcestr_commerce_solana.errors import SolanaCommerceError, SolanaErrorCode


class CapabilityIssue(BaseModel):
    """Returns the bearer once and the digest that may be persisted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    public_id: UUID
    secret: str = Field(min_length=43, max_length=128)
    secret_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime


class SolanaCapabilityService:
    """Issues 256-bit capabilities and builds non-persistent Solana Pay URIs."""

    secret_pattern = re.compile(r"^[A-Za-z0-9_-]{43}$")

    def issue(self, expires_at: datetime) -> CapabilityIssue:
        """Creates a capability whose raw secret must be returned only once."""
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("Capability expiry must be timezone-aware.")
        secret = secrets.token_urlsafe(32)
        return CapabilityIssue(
            public_id=uuid4(),
            secret=secret,
            secret_sha256=self.digest(secret),
            expires_at=expires_at,
        )

    @staticmethod
    def digest(secret: str) -> str:
        """Creates the only capability representation allowed in persistence."""
        if SolanaCapabilityService.secret_pattern.fullmatch(secret) is None:
            raise SolanaCommerceError(SolanaErrorCode.CAPABILITY_INVALID, "Transaction request is unavailable.")
        return hashlib.sha256(secret.encode("ascii")).hexdigest()

    @classmethod
    def matches(cls, secret: str, expected_sha256: str) -> bool:
        """Compares a presented capability with a stored digest in constant time."""
        return hmac.compare_digest(cls.digest(secret), expected_sha256)

    @staticmethod
    def transaction_request_uri(public_base_url: str, secret: str) -> str:
        """Builds the canonical Solana Pay URI without storing it."""
        encoded = quote(secret, safe="")
        return f"solana:{public_base_url.rstrip('/')}/commerce/solana/transaction-requests/{encoded}"
