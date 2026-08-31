from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orcestr_commerce_solana.constants import MAINNET_GENESIS_HASH
from orcestr_commerce_solana.schemas.assets import SolanaCluster


class SolanaRpcConfig(BaseModel):
    """Configures a standard HTTP JSON-RPC pool with exact cluster identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    genesis_hash: str = Field(min_length=32, max_length=64)
    endpoints: tuple[str, ...] = ("https://api.mainnet-beta.solana.com",)
    timeout_seconds: float = Field(default=12.0, gt=0, le=60)
    max_attempts_per_endpoint: int = Field(default=2, ge=1, le=5)
    base_backoff_seconds: float = Field(default=0.2, ge=0, le=5)
    circuit_break_seconds: float = Field(default=15.0, ge=0, le=300)

    @field_validator("endpoints")
    @classmethod
    def validate_endpoints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Accepts only explicit HTTPS URLs and preserves fallback order."""
        if not value:
            raise ValueError("At least one JSON-RPC endpoint is required.")
        if len(set(value)) != len(value):
            raise ValueError("JSON-RPC endpoints must be unique.")
        for endpoint in value:
            parsed = urlparse(endpoint)
            is_loopback_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (parsed.scheme != "https" and not is_loopback_http) or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("JSON-RPC endpoints must use HTTPS, except HTTP loopback in local development.")
            if parsed.query or parsed.fragment:
                raise ValueError("JSON-RPC endpoint query strings and fragments are not supported.")
        return value


class SolanaCommerceConfig(BaseModel):
    """Captures host policy that must be snapshotted into each payment intent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster: SolanaCluster = SolanaCluster.MAINNET_BETA
    rpc: SolanaRpcConfig = SolanaRpcConfig(genesis_hash=MAINNET_GENESIS_HASH)
    public_base_url: str
    merchant_label: str = Field(min_length=1, max_length=100)
    explorer_base_url: str = "https://explorer.solana.com"
    intent_ttl: timedelta = Field(default=timedelta(minutes=15), gt=timedelta(seconds=30), le=timedelta(days=1))
    capability_ttl: timedelta = Field(default=timedelta(minutes=10), gt=timedelta(seconds=30), le=timedelta(hours=1))
    issuance_safety_margin: timedelta = Field(default=timedelta(seconds=30), ge=timedelta(0), le=timedelta(minutes=5))
    max_active_capabilities: int = Field(default=3, ge=1, le=10)
    max_issuances_per_intent: int = Field(default=16, ge=1, le=100)
    max_candidate_checks_per_intent: int = Field(default=16, ge=1, le=100)
    terminal_reconciliation_grace: timedelta = Field(
        default=timedelta(minutes=5),
        ge=timedelta(minutes=1),
        le=timedelta(hours=1),
    )
    max_reconciliation_batch: int = Field(default=100, ge=1, le=1000)
    max_candidate_verifications_per_intent: int = Field(default=16, ge=1, le=64)
    max_candidate_verifications_per_pass: int = Field(default=32, ge=1, le=256)
    enable_native_sol: bool = False
    enable_token_2022: bool = True

    @field_validator("public_base_url", "explorer_base_url")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        """Rejects request-derived, credential-bearing, or ambiguous public origins."""
        parsed = urlparse(value)
        is_loopback_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parsed.scheme != "https" and not is_loopback_http) or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Public URLs must use HTTPS, except HTTP loopback in local development.")
        if parsed.query or parsed.fragment:
            raise ValueError("Public URLs cannot contain query strings or fragments.")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_feature_set(self) -> SolanaCommerceConfig:
        """Refuses a checkout configuration with no enabled settlement asset kind."""
        if self.rpc.genesis_hash != self.cluster.genesis_hash:
            raise ValueError("Configured cluster name and RPC genesis hash do not match.")
        if not self.enable_native_sol and not self.enable_token_2022:
            raise ValueError("At least one settlement asset kind must be enabled.")
        if self.max_candidate_verifications_per_pass < self.max_candidate_verifications_per_intent:
            raise ValueError(
                "The reconciliation pass budget must cover at least one complete intent budget."
            )
        return self
