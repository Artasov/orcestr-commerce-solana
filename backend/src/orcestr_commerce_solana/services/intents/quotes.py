from __future__ import annotations

from collections.abc import Mapping

from orcestr_commerce_solana.amounts import SolanaAmountCodec
from orcestr_commerce_solana.errors import SolanaCommerceError, SolanaErrorCode
from orcestr_commerce_solana.services.intents.ports import SettlementQuoteRequest, SettlementQuoteResult


class FixedSettlementQuoteProvider:
    """Uses explicit per-product and per-option raw prices as a fixed strategy."""

    def __init__(self, raw_prices: Mapping[tuple[str, str], str], *, version: str = "1") -> None:
        self._validate_version(version)
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
            rounding="exact",
        )

    async def is_available(self, request: SettlementQuoteRequest) -> bool:
        """Returns whether an explicit raw price exists and fits this asset policy."""
        raw = self._raw_prices.get((request.price_key, request.asset.option_id))
        if raw is None:
            return False
        return int(request.asset.minimum_raw_amount) <= int(raw) <= int(request.asset.maximum_raw_amount)

    @staticmethod
    def _validate_version(version: str) -> None:
        """Checks quote provenance before any payment can use the strategy."""
        if not isinstance(version, str) or not version or len(version) > 100:
            raise ValueError("Settlement quote version must contain between 1 and 100 characters.")


class OrderSnapshotSettlementQuoteProvider:
    """Converts an exact DB-priced order amount into raw units of the same asset."""

    def __init__(self, asset_currencies: Mapping[str, str], *, version: str = "1") -> None:
        if not isinstance(version, str) or not version or len(version) > 100:
            raise ValueError("Settlement quote version must contain between 1 and 100 characters.")
        for option_id, currency in asset_currencies.items():
            if (
                not isinstance(option_id, str)
                or not option_id
                or option_id != option_id.strip()
                or len(option_id) > 100
            ):
                raise ValueError("Order snapshot asset option ids must be canonical non-empty strings.")
            if (
                not isinstance(currency, str)
                or not currency
                or currency != currency.strip()
                or currency != currency.upper()
                or len(currency) > 12
            ):
                raise ValueError("Order snapshot commerce currencies must be canonical non-empty strings.")
        self._asset_currencies = dict(asset_currencies)
        self._version = version

    async def quote(self, request: SettlementQuoteRequest) -> SettlementQuoteResult:
        """Freezes the exact order amount after checking its configured asset currency."""
        raw = self._raw_amount(request)
        return SettlementQuoteResult(
            expected_raw_amount=str(raw),
            source="order_snapshot",
            version=self._version,
            rounding="exact",
        )

    async def is_available(self, request: SettlementQuoteRequest) -> bool:
        """Returns whether the exact order snapshot is payable by this asset option."""
        try:
            self._raw_amount(request)
        except (KeyError, ValueError, SolanaCommerceError):
            return False
        return True

    def _raw_amount(self, request: SettlementQuoteRequest) -> int:
        """Validates currency identity and exact positive u64 option bounds."""
        currency = self._asset_currencies.get(request.asset.option_id)
        if currency is None:
            raise KeyError(f"No order snapshot currency configured for Solana asset {request.asset.option_id}.")
        if request.commercial_currency != currency:
            raise ValueError(
                f"Commerce currency must exactly match {currency} for Solana asset {request.asset.option_id}.",
            )
        raw = SolanaAmountCodec.parse(request.commercial_amount, request.asset.decimals)
        if raw <= 0:
            raise SolanaCommerceError(SolanaErrorCode.INVALID_AMOUNT, "Order snapshot amount must be positive.")
        minimum = int(request.asset.minimum_raw_amount)
        maximum = int(request.asset.maximum_raw_amount)
        if raw < minimum or raw > maximum:
            raise SolanaCommerceError(
                SolanaErrorCode.INVALID_AMOUNT,
                "Order snapshot amount is outside the configured Solana asset limits.",
            )
        return raw
