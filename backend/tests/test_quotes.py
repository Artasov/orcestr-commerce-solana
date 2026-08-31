from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from orcestr_commerce_solana import (
    FixedSettlementQuoteProvider,
    OrderSnapshotSettlementQuoteProvider,
    SettlementQuoteRequest,
    SettlementQuoteResult,
    SettlementQuoteSnapshot,
)
from orcestr_commerce_solana.constants import MAINNET_GENESIS_HASH, ORCESTR_TOKEN_MINT, TOKEN_2022_PROGRAM_ID
from orcestr_commerce_solana.errors import SolanaCommerceError, SolanaErrorCode
from orcestr_commerce_solana.schemas.assets import SolanaAssetKind, SolanaAssetPolicy


class TestOrderSnapshotSettlementQuoteProvider:
    """Checks exact conversion of host-owned DB prices into Solana raw units."""

    @pytest.mark.asyncio
    async def test_converts_exact_orcestr_decimal_without_rate_or_rounding(self) -> None:
        provider = OrderSnapshotSettlementQuoteProvider(
            {"solana_orcestr": "ORCESTR"},
            version="catalog-v1",
        )
        amount = Decimal("2500.125000")
        request = self._request(format(amount, "f"))

        result = await provider.quote(request)

        assert result.expected_raw_amount == "2500125000"
        assert result.source == "order_snapshot"
        assert result.version == "catalog-v1"
        assert result.rate_numerator is None
        assert result.rate_denominator is None
        assert result.rounding == "exact"
        assert await provider.is_available(request) is True

    @pytest.mark.asyncio
    async def test_currency_must_exactly_match_option_allowlist(self) -> None:
        provider = OrderSnapshotSettlementQuoteProvider({"solana_orcestr": "ORCESTR"})

        for currency in ("USD", "orcestr", "ORCESTR "):
            request = self._request("1", currency=currency)

            assert await provider.is_available(request) is False
            with pytest.raises(ValueError, match="exactly match ORCESTR"):
                await provider.quote(request)

    @pytest.mark.asyncio
    async def test_display_symbol_does_not_define_commercial_currency_identity(self) -> None:
        provider = OrderSnapshotSettlementQuoteProvider({"solana_orcestr": "ORCESTR"})
        request = self._request("1", symbol="Display name only")

        result = await provider.quote(request)

        assert result.expected_raw_amount == "1000000"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("amount", "minimum", "maximum"),
        [
            ("0", "1", "18446744073709551615"),
            ("0.999999", "1000000", "2000000"),
            ("2.000001", "1000000", "2000000"),
            ("18446744073709.551616", "1", "18446744073709551615"),
        ],
    )
    async def test_rejects_zero_u64_overflow_and_option_bounds(
        self,
        amount: str,
        minimum: str,
        maximum: str,
    ) -> None:
        provider = OrderSnapshotSettlementQuoteProvider({"solana_orcestr": "ORCESTR"})
        request = self._request(amount, minimum=minimum, maximum=maximum)

        assert await provider.is_available(request) is False
        with pytest.raises(SolanaCommerceError) as error:
            await provider.quote(request)

        assert error.value.code == SolanaErrorCode.INVALID_AMOUNT

    @pytest.mark.asyncio
    async def test_rejects_fractional_precision_instead_of_rounding(self) -> None:
        provider = OrderSnapshotSettlementQuoteProvider({"solana_orcestr": "ORCESTR"})
        request = self._request("1.0000001")

        assert await provider.is_available(request) is False
        with pytest.raises(SolanaCommerceError) as error:
            await provider.quote(request)

        assert error.value.code == SolanaErrorCode.INVALID_AMOUNT
        assert "fractional digits" in str(error.value)

    @pytest.mark.asyncio
    async def test_accepts_exact_minimum_and_maximum(self) -> None:
        provider = OrderSnapshotSettlementQuoteProvider({"solana_orcestr": "ORCESTR"})
        requests = (
            self._request("1", minimum="1000000", maximum="2000000"),
            self._request("2", minimum="1000000", maximum="2000000"),
        )

        assert [
            (await provider.quote(request)).expected_raw_amount
            for request in requests
        ] == ["1000000", "2000000"]

    @pytest.mark.asyncio
    async def test_accepts_database_scale_zeroes_beyond_asset_decimals(self) -> None:
        provider = OrderSnapshotSettlementQuoteProvider({"solana_orcestr": "ORCESTR"})
        request = self._request("1.230000", decimals=2)

        result = await provider.quote(request)

        assert result.expected_raw_amount == "123"
        assert await provider.is_available(request) is True

    @staticmethod
    def _request(
        amount: str,
        *,
        currency: str = "ORCESTR",
        symbol: str = "ORCESTR",
        minimum: str = "1",
        maximum: str = "18446744073709551615",
        decimals: int = 6,
    ) -> SettlementQuoteRequest:
        return SettlementQuoteRequest(
            order_public_id=uuid4(),
            price_key="beauty.credits.100",
            commercial_amount=amount,
            commercial_currency=currency,
            asset=SolanaAssetPolicy(
                option_id="solana_orcestr",
                cluster="mainnet-beta",
                genesis_hash=MAINNET_GENESIS_HASH,
                kind=SolanaAssetKind.TOKEN,
                mint=ORCESTR_TOKEN_MINT,
                token_program=TOKEN_2022_PROGRAM_ID,
                decimals=decimals,
                display_name="Orcestr",
                symbol=symbol,
                minimum_raw_amount=minimum,
                maximum_raw_amount=maximum,
            ),
        )


class TestQuoteRoundingContract:
    """Keeps exact conversion explicit across provider and persistence contracts."""

    @pytest.mark.asyncio
    async def test_fixed_raw_strategy_reports_exact_rounding(self) -> None:
        request = TestOrderSnapshotSettlementQuoteProvider._request("1")
        provider = FixedSettlementQuoteProvider({("beauty.credits.100", "solana_orcestr"): "1000000"})

        result = await provider.quote(request)

        assert result.rounding == "exact"

    def test_rounding_has_no_implicit_legacy_default(self) -> None:
        with pytest.raises(ValidationError):
            SettlementQuoteResult(
                expected_raw_amount="1000000",
                source="test",
                version="1",
            )
        with pytest.raises(ValidationError):
            SettlementQuoteSnapshot(
                source="test",
                version="1",
                commercial_amount="1",
                commercial_currency="ORCESTR",
            )

        snapshot = SettlementQuoteSnapshot(
            source="order_snapshot",
            version="1",
            commercial_amount="1",
            commercial_currency="ORCESTR",
            rounding="exact",
        )
        assert snapshot.rounding == "exact"
