from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from orcestr_commerce_solana.schemas.assets import SolanaAssetPolicy, ValidatedSolanaAsset


class RecipientContext(BaseModel):
    """Carries host identities without importing application ORM models."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order_public_id: UUID
    payer_actor_key: str = Field(min_length=1, max_length=100)
    payee_key: str | None = Field(default=None, max_length=100)


class RecipientSnapshot(BaseModel):
    """Pins the wallet and optional Token-2022 ATA selected by host policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wallet: str = Field(min_length=32, max_length=44)
    token_account: str | None = Field(default=None, min_length=32, max_length=44)
    policy_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}$")


class RecipientResolver(Protocol):
    """Selects treasury or P2P recipient per intent without hardcoded wallets."""

    async def resolve(
        self,
        context: RecipientContext,
        asset: ValidatedSolanaAsset,
    ) -> RecipientSnapshot:
        """Returns a previously validated recipient snapshot."""
        ...

    async def is_available(
        self,
        context: RecipientContext,
        asset: ValidatedSolanaAsset,
    ) -> bool:
        """Checks whether recipient policy can resolve before publishing an option."""
        ...


class StaticRecipientResolver:
    """Resolves one configured treasury wallet for platform product checkout."""

    def __init__(self, recipients: dict[str, RecipientSnapshot]) -> None:
        self._recipients = dict(recipients)

    async def resolve(
        self,
        context: RecipientContext,
        asset: ValidatedSolanaAsset,
    ) -> RecipientSnapshot:
        """Returns the treasury mapping for the selected immutable asset option."""
        _ = context
        recipient = self._recipients.get(asset.policy.option_id)
        if recipient is None:
            raise KeyError(f"No recipient configured for Solana asset option {asset.policy.option_id}.")
        return recipient

    async def is_available(
        self,
        context: RecipientContext,
        asset: ValidatedSolanaAsset,
    ) -> bool:
        """Returns whether the treasury mapping exists for this option."""
        _ = context
        return asset.policy.option_id in self._recipients


class SettlementQuoteRequest(BaseModel):
    """Provides server-side commercial facts to an injected quote provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order_public_id: UUID
    price_key: str = Field(min_length=1, max_length=100)
    commercial_amount: str
    commercial_currency: str
    asset: SolanaAssetPolicy


class SettlementQuoteResult(BaseModel):
    """Returns one exact immutable raw amount and its audit snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_raw_amount: str = Field(pattern=r"^[0-9]+$")
    source: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    rate_numerator: str | None = Field(default=None, pattern=r"^[0-9]+$")
    rate_denominator: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")
    rounding: str = Field(default="down", pattern=r"^(down|half_up|up)$")


class SettlementQuoteProvider(Protocol):
    """Computes exact raw units from host-owned commercial order facts."""

    async def quote(self, request: SettlementQuoteRequest) -> SettlementQuoteResult:
        """Returns a frozen quote; clients never supply the result."""
        ...

    async def is_available(self, request: SettlementQuoteRequest) -> bool:
        """Checks quote availability before the option is shown to a client."""
        ...


class SettlementPriceKeyResolver(Protocol):
    """Resolves a stable host product/plan/pack identity for settlement pricing."""

    async def resolve(
        self,
        session: AsyncSession,
        order: object,
        actor: object,
    ) -> str | None:
        """Returns a product-level key, or None when this order is not payable by Solana."""
        ...

class FixedSettlementQuoteProvider:
    """Uses explicit per-option raw prices for the safe first production flow."""

    def __init__(self, raw_prices: dict[tuple[str, str], str], *, version: str = "1") -> None:
        for key, raw in raw_prices.items():
            if (
                len(key) != 2
                or not key[0]
                or not key[1]
                or not raw.isdigit()
                or raw != str(int(raw))
                or int(raw) < 1
                or int(raw) > 18_446_744_073_709_551_615
            ):
                raise ValueError("Fixed Solana raw prices require non-empty keys and canonical positive u64 values.")
        self._raw_prices = dict(raw_prices)
        self._version = version

    async def quote(self, request: SettlementQuoteRequest) -> SettlementQuoteResult:
        """Returns the configured raw amount without market or float arithmetic."""
        raw = self._raw_prices.get((request.price_key, request.asset.option_id))
        if raw is None:
            raise KeyError(
                f"No fixed quote configured for price key {request.price_key} and Solana asset {request.asset.option_id}.",
            )
        return SettlementQuoteResult(
            expected_raw_amount=raw,
            source="fixed",
            version=self._version,
        )

    async def is_available(self, request: SettlementQuoteRequest) -> bool:
        """Returns whether an explicit raw price exists and fits this asset policy."""
        raw = self._raw_prices.get((request.price_key, request.asset.option_id))
        if raw is None:
            return False
        return int(request.asset.minimum_raw_amount) <= int(raw) <= int(request.asset.maximum_raw_amount)
