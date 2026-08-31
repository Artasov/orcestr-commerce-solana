from __future__ import annotations

import re

from orcestr_commerce_solana.constants import MAX_U64
from orcestr_commerce_solana.errors import SolanaCommerceError, SolanaErrorCode


class SolanaAmountCodec:
    """Converts display amounts to exact u64 base units without float arithmetic."""

    pattern = re.compile(r"^(0|[1-9][0-9]*)(?:\.([0-9]+))?$")

    @classmethod
    def parse(cls, value: str, decimals: int) -> int:
        """Parses a canonical decimal string using the asset decimals snapshot."""
        cls.validate_decimals(decimals)
        match = cls.pattern.fullmatch(value)
        if match is None:
            raise SolanaCommerceError(SolanaErrorCode.INVALID_AMOUNT, "Amount must be a plain non-negative decimal string.")
        fraction = match.group(2) or ""
        if len(fraction) > decimals:
            raise SolanaCommerceError(SolanaErrorCode.INVALID_AMOUNT, "Amount has more fractional digits than the asset supports.")
        raw = int(match.group(1)) * 10**decimals
        raw += int(fraction.ljust(decimals, "0") or "0")
        cls.validate_raw(raw)
        return raw

    @classmethod
    def format(cls, raw: int, decimals: int) -> str:
        """Formats base units as a plain decimal string without exponent notation."""
        cls.validate_decimals(decimals)
        cls.validate_raw(raw)
        if decimals == 0:
            return str(raw)
        scale = 10**decimals
        whole, fraction = divmod(raw, scale)
        if fraction == 0:
            return str(whole)
        return f"{whole}.{fraction:0{decimals}d}".rstrip("0")

    @staticmethod
    def validate_raw(raw: int) -> None:
        """Checks the unsigned Solana token amount range."""
        if isinstance(raw, bool) or raw < 0 or raw > MAX_U64:
            raise SolanaCommerceError(SolanaErrorCode.INVALID_AMOUNT, "Raw amount is outside the Solana u64 range.")

    @staticmethod
    def validate_decimals(decimals: int) -> None:
        """Checks the decimals range encoded by Mint and TransferChecked."""
        if isinstance(decimals, bool) or decimals < 0 or decimals > 255:
            raise SolanaCommerceError(SolanaErrorCode.INVALID_AMOUNT, "Decimals must be an integer between 0 and 255.")
