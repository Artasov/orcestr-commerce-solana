from __future__ import annotations

from enum import StrEnum


class SolanaErrorCode(StrEnum):
    """Stable error codes safe for API responses, metrics, and reconciliation."""

    INVALID_ADDRESS = "invalid_address"
    INVALID_AMOUNT = "invalid_amount"
    INVALID_CONFIGURATION = "invalid_configuration"
    WRONG_CLUSTER = "wrong_cluster"
    RPC_TEMPORARILY_UNAVAILABLE = "rpc_temporarily_unavailable"
    RPC_INVALID_RESPONSE = "rpc_invalid_response"
    ASSET_NOT_FOUND = "asset_not_found"
    ASSET_INACTIVE = "asset_inactive"
    WRONG_MINT_OWNER = "wrong_mint_owner"
    LEGACY_TOKEN_PROGRAM = "legacy_token_program"
    WRONG_DECIMALS = "wrong_decimals"
    UNSUPPORTED_TOKEN_EXTENSION = "unsupported_token_extension"
    UNSAFE_MINT_AUTHORITY = "unsafe_mint_authority"
    UNSAFE_FREEZE_AUTHORITY = "unsafe_freeze_authority"
    INVALID_TOKEN_ACCOUNT = "invalid_token_account"
    CAPABILITY_INVALID = "capability_invalid"
    CAPABILITY_EXPIRED = "capability_expired"
    ISSUANCE_LIMIT_REACHED = "issuance_limit_reached"
    CANDIDATE_LIMIT_REACHED = "candidate_limit_reached"
    REFERENCE_CANDIDATE_BUDGET_EXCEEDED = "reference_candidate_budget_exceeded"
    CANDIDATE_VERIFICATION_GLOBAL_BUDGET_EXHAUSTED = (
        "candidate_verification_global_budget_exhausted"
    )
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INTENT_EXPIRED = "intent_expired"
    TRANSACTION_NOT_FOUND = "transaction_not_found"
    TRANSACTION_FAILED = "transaction_failed"
    TRANSACTION_NOT_FINAL = "transaction_not_final"
    TRANSACTION_DECODE_FAILED = "transaction_decode_failed"
    SIGNATURE_MISMATCH = "signature_mismatch"
    SIGNATURE_ALREADY_USED = "signature_already_used"
    ISSUANCE_MISMATCH = "issuance_mismatch"
    REFERENCE_MISSING = "reference_missing"
    WRONG_PROGRAM = "wrong_program"
    WRONG_MINT = "wrong_mint"
    WRONG_RECIPIENT = "wrong_recipient"
    WRONG_AMOUNT = "wrong_amount"
    WRONG_DECIMALS_IN_INSTRUCTION = "wrong_decimals_in_instruction"
    INVALID_BALANCE_DELTA = "invalid_balance_delta"
    MULTIPLE_PAYMENT_INSTRUCTIONS = "multiple_payment_instructions"
    UNSUPPORTED_INSTRUCTION = "unsupported_instruction"
    UNSUPPORTED_CPI_OR_SWAP = "unsupported_cpi_or_swap"
    LATE_PAYMENT = "late_payment"
    BLOCK_TIME_UNAVAILABLE = "block_time_unavailable"


class SolanaCommerceError(Exception):
    """Base typed failure carrying a stable, non-sensitive reason code."""

    def __init__(self, code: SolanaErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class SolanaConfigurationError(SolanaCommerceError):
    """Reports an invalid host configuration before accepting payments."""


class SolanaAssetError(SolanaCommerceError):
    """Reports a rejected mint, token account, or asset policy."""


class SolanaRpcError(SolanaCommerceError):
    """Base failure produced by a JSON-RPC transport."""


class SolanaRpcUnavailableError(SolanaRpcError):
    """Marks a retryable outage, timeout, rate limit, or unavailable result."""


class SolanaRpcResponseError(SolanaRpcError):
    """Marks a malformed or semantically invalid JSON-RPC response."""
