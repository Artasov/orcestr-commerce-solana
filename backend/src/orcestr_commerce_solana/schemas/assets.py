from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from solders.pubkey import Pubkey

from orcestr_commerce_solana.constants import (
    DEVNET_GENESIS_HASH,
    MAINNET_GENESIS_HASH,
    TESTNET_GENESIS_HASH,
    TOKEN_2022_PROGRAM_ID,
)


class SolanaCluster(StrEnum):
    """Named public clusters supported by wallet and verifier contracts."""

    MAINNET_BETA = "mainnet-beta"
    DEVNET = "devnet"
    TESTNET = "testnet"

    @property
    def genesis_hash(self) -> str:
        """Returns the consensus identity expected for this cluster name."""
        return {
            self.MAINNET_BETA: MAINNET_GENESIS_HASH,
            self.DEVNET: DEVNET_GENESIS_HASH,
            self.TESTNET: TESTNET_GENESIS_HASH,
        }[self]


class SolanaAssetKind(StrEnum):
    """Settlement primitives supported by the first release."""

    NATIVE = "native"
    TOKEN = "token"


class SolanaAssetStatus(StrEnum):
    """Controls whether an immutable option can create new intents."""

    ACTIVE = "active"
    DISABLED = "disabled"


class SolanaCommitment(StrEnum):
    """Commitments that may appear in a payment snapshot."""

    CONFIRMED = "confirmed"
    FINALIZED = "finalized"

    @property
    def rank(self) -> int:
        """Returns a stable ordering for finality comparisons."""
        return {self.CONFIRMED: 1, self.FINALIZED: 2}[self]


SettlementRounding = Literal["exact", "down", "half_up", "up"]


class TokenExtensionSnapshot(BaseModel):
    """Records one decoded Token-2022 extension without trusting metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type_id: int = Field(ge=0, le=65_535)
    name: str = Field(min_length=1, max_length=100)


class SolanaAssetPolicy(BaseModel):
    """Defines one server-owned immutable checkout option and its risk policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    option_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    cluster: SolanaCluster
    genesis_hash: str = Field(min_length=32, max_length=64)
    kind: SolanaAssetKind
    mint: str | None = Field(default=None, min_length=32, max_length=44)
    token_program: str | None = Field(default=None, min_length=32, max_length=44)
    decimals: int = Field(ge=0, le=255)
    display_name: str = Field(min_length=1, max_length=100)
    symbol: str = Field(min_length=1, max_length=20)
    status: SolanaAssetStatus = SolanaAssetStatus.ACTIVE
    required_commitment: Literal[SolanaCommitment.FINALIZED] = SolanaCommitment.FINALIZED
    minimum_raw_amount: str = Field(default="1", pattern=r"^[0-9]+$")
    maximum_raw_amount: str = Field(default="18446744073709551615", pattern=r"^[0-9]+$")
    allow_mint_authority: bool = False
    allow_freeze_authority: bool = False
    allowed_mint_extensions: frozenset[int] = frozenset({18, 19, 20, 21, 22, 23})

    @field_validator("mint")
    @classmethod
    def validate_mint_address(cls, value: str | None) -> str | None:
        """Checks canonical base58 mint identity before RPC or persistence."""
        if value is None:
            return None
        try:
            parsed = Pubkey.from_string(value)
        except ValueError as exc:
            raise ValueError("Mint must be a canonical Solana public key.") from exc
        if str(parsed) != value:
            raise ValueError("Mint must be a canonical Solana public key.")
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> SolanaAssetPolicy:
        """Keeps native SOL and Token-2022 identities structurally distinct."""
        if self.genesis_hash != self.cluster.genesis_hash:
            raise ValueError("Asset cluster name and genesis hash do not match.")
        minimum = int(self.minimum_raw_amount)
        maximum = int(self.maximum_raw_amount)
        if minimum < 1 or maximum < minimum or maximum > 18_446_744_073_709_551_615:
            raise ValueError("Asset amount limits must fit the Solana u64 range.")
        if self.kind == SolanaAssetKind.NATIVE:
            if self.mint is not None or self.token_program is not None or self.decimals != 9:
                raise ValueError("Native SOL must use decimals=9 and cannot define a mint or token program.")
            return self
        if self.mint is None:
            raise ValueError("Token assets require an exact mint address.")
        if self.token_program != TOKEN_2022_PROGRAM_ID:
            raise ValueError("Only the Token-2022 Program is supported.")
        return self


class ValidatedSolanaAsset(BaseModel):
    """Pins on-chain facts used to activate an asset option."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: SolanaAssetPolicy
    mint_authority: str | None = None
    freeze_authority: str | None = None
    extensions: tuple[TokenExtensionSnapshot, ...] = ()
    account_data_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_activation_evidence(self) -> ValidatedSolanaAsset:
        """Blocks an active token snapshot that did not pass on-chain byte validation."""
        if (
            self.policy.kind == SolanaAssetKind.TOKEN
            and self.policy.status == SolanaAssetStatus.ACTIVE
            and self.account_data_sha256 is None
        ):
            raise ValueError("Active Token-2022 assets require a validated account-data hash.")
        return self


class SettlementQuoteSnapshot(BaseModel):
    """Preserves exact quote operands and provenance without recomputation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    commercial_amount: str = Field(pattern=r"^(0|[1-9][0-9]*)(?:\.[0-9]+)?$")
    commercial_currency: str = Field(min_length=1, max_length=12)
    rate_numerator: str | None = Field(default=None, pattern=r"^[0-9]+$")
    rate_denominator: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")
    rounding: SettlementRounding


class SettlementSnapshot(BaseModel):
    """Defines the immutable on-chain contract for one payment attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster: SolanaCluster
    genesis_hash: str
    asset_option_id: str
    kind: SolanaAssetKind
    asset_name: str = Field(min_length=1, max_length=100)
    asset_symbol: str = Field(min_length=1, max_length=20)
    mint: str | None = None
    token_program: str | None = None
    decimals: int = Field(ge=0, le=255)
    recipient_wallet: str = Field(min_length=32, max_length=44)
    recipient_token_account: str | None = Field(default=None, min_length=32, max_length=44)
    recipient_policy_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}$")
    expected_raw_amount: str = Field(pattern=r"^[0-9]+$")
    display_amount: str = Field(pattern=r"^(0|[1-9][0-9]*)(?:\.[0-9]+)?$")
    reference: str = Field(min_length=32, max_length=44)
    required_commitment: Literal[SolanaCommitment.FINALIZED] = SolanaCommitment.FINALIZED
    quote: SettlementQuoteSnapshot
    memo: str | None = Field(default=None, max_length=120)

    @field_validator("recipient_wallet", "recipient_token_account", "reference")
    @classmethod
    def validate_public_keys(cls, value: str | None) -> str | None:
        """Checks every settlement address before it can reach persistence or RPC."""
        if value is None:
            return None
        try:
            parsed = Pubkey.from_string(value)
        except ValueError as exc:
            raise ValueError("Settlement addresses must be canonical Solana public keys.") from exc
        if str(parsed) != value:
            raise ValueError("Settlement addresses must be canonical Solana public keys.")
        return value

    @model_validator(mode="after")
    def validate_asset(self) -> SettlementSnapshot:
        """Rejects snapshots that could blur native SOL and Token-2022."""
        if self.genesis_hash != self.cluster.genesis_hash:
            raise ValueError("Settlement cluster name and genesis hash do not match.")
        raw = int(self.expected_raw_amount)
        if raw <= 0 or raw > 18_446_744_073_709_551_615:
            raise ValueError("Expected raw amount must be positive and fit u64.")
        from orcestr_commerce_solana.amounts import SolanaAmountCodec

        if self.display_amount != SolanaAmountCodec.format(raw, self.decimals):
            raise ValueError("Display amount does not match expected raw amount and decimals.")
        if self.kind == SolanaAssetKind.NATIVE:
            if self.decimals != 9 or self.mint is not None or self.token_program is not None:
                raise ValueError("Native SOL snapshot is inconsistent.")
            if self.recipient_token_account is not None:
                raise ValueError("Native SOL does not use a recipient token account.")
        else:
            if not self.mint or self.token_program != TOKEN_2022_PROGRAM_ID or not self.recipient_token_account:
                raise ValueError("Token snapshot must pin mint, Token-2022, and recipient token account.")
        return self
