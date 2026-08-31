from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from solders.hash import Hash
from solders.keypair import Keypair
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from orcestr_commerce_solana.constants import MAINNET_GENESIS_HASH, ORCESTR_TOKEN_MINT, TOKEN_2022_PROGRAM_ID
from orcestr_commerce_solana.rpc.types import (
    RpcAccountInfo,
    RpcLatestBlockhash,
    RpcSignatureInfo,
    RpcSignatureStatus,
    RpcTransaction,
)
from orcestr_commerce_solana.schemas.assets import (
    SettlementQuoteSnapshot,
    SettlementSnapshot,
    SolanaAssetKind,
    SolanaCommitment,
)
from orcestr_commerce_solana.schemas.transactions import TransactionIssuanceSnapshot, TransactionVersion


class FixtureLoader:
    """Loads frozen JSON fixtures without performing network requests."""

    root = Path(__file__).parent / "fixtures"

    @classmethod
    def load(cls, name: str) -> dict[str, Any]:
        """Returns one fixture decoded as UTF-8 JSON."""
        return json.loads((cls.root / name).read_text(encoding="utf-8"))


class FakeRpc:
    """Implements the package RPC protocol with deterministic in-memory values."""

    def __init__(self) -> None:
        self.genesis_hash = MAINNET_GENESIS_HASH
        self.accounts: dict[str, RpcAccountInfo] = {}
        self.latest = RpcLatestBlockhash(
            blockhash=str(Hash.default()),
            last_valid_block_height=1000,
            context_slot=800,
        )
        self.status: RpcSignatureStatus | None = None
        self.transaction: RpcTransaction | None = None
        self.history: tuple[RpcSignatureInfo, ...] = ()

    async def check_cluster(self) -> str:
        return self.genesis_hash

    async def get_account_info(self, address: str, commitment: SolanaCommitment) -> RpcAccountInfo | None:
        _ = commitment
        return self.accounts.get(address)

    async def get_latest_blockhash(self, commitment: SolanaCommitment) -> RpcLatestBlockhash:
        _ = commitment
        return self.latest

    async def get_block_height(self, commitment: SolanaCommitment) -> int:
        _ = commitment
        return 900

    async def get_signature_status(self, signature: str) -> RpcSignatureStatus | None:
        _ = signature
        return self.status

    async def get_transaction(self, signature: str, commitment: SolanaCommitment) -> RpcTransaction | None:
        _ = signature
        _ = commitment
        return self.transaction

    async def get_signatures_for_address(
        self,
        address: str,
        *,
        before: str | None = None,
        limit: int = 100,
        commitment: SolanaCommitment = SolanaCommitment.CONFIRMED,
    ) -> tuple[RpcSignatureInfo, ...]:
        _ = address
        _ = before
        _ = limit
        _ = commitment
        return self.history


class MemoryUsedSignatures:
    """Provides deterministic signature uniqueness for verifier tests."""

    def __init__(self, used: set[str] | None = None) -> None:
        self.used = used or set()

    async def is_used(self, cluster: str, signature: str, intent_public_id: object) -> bool:
        _ = cluster
        _ = intent_public_id
        return signature in self.used


class TransactionFixtureFactory:
    """Signs builder output and creates matching raw RPC metadata."""

    @staticmethod
    def sign(unsigned_base64: str, signer: Keypair) -> tuple[bytes, str, VersionedTransaction]:
        """Signs issued message bytes with the exact configured payer."""
        unsigned = VersionedTransaction.from_bytes(base64.b64decode(unsigned_base64))
        signed = VersionedTransaction(unsigned.message, [signer])
        signature = signed.signatures[0]
        return bytes(signed), str(signature), signed

    @staticmethod
    def native_meta(
        transaction: VersionedTransaction,
        recipient: str,
        amount: int,
        *,
        inner_instructions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Builds exact recipient lamport balances for a direct System transfer."""
        keys = [str(key) for key in transaction.message.account_keys]
        recipient_index = keys.index(recipient)
        pre = [1_000_000 for _ in keys]
        post = list(pre)
        post[recipient_index] += amount
        return {
            "err": None,
            "innerInstructions": inner_instructions,
            "loadedAddresses": {"writable": [], "readonly": []},
            "preBalances": pre,
            "postBalances": post,
            "preTokenBalances": [],
            "postTokenBalances": [],
        }

    @staticmethod
    def token_meta(
        transaction: VersionedTransaction,
        source: str,
        destination: str,
        mint: str,
        decimals: int,
        amount: int,
        *,
        inner_instructions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Builds exact source and destination token deltas in raw units."""
        keys = [str(key) for key in transaction.message.account_keys]
        source_index = keys.index(source)
        destination_index = keys.index(destination)
        token_balance = lambda index, raw: {
            "accountIndex": index,
            "mint": mint,
            "programId": TOKEN_2022_PROGRAM_ID,
            "uiTokenAmount": {"amount": str(raw), "decimals": decimals, "uiAmount": None, "uiAmountString": ""},
        }
        return {
            "err": None,
            "innerInstructions": inner_instructions,
            "loadedAddresses": {"writable": [], "readonly": []},
            "preBalances": [1_000_000 for _ in keys],
            "postBalances": [1_000_000 for _ in keys],
            "preTokenBalances": [token_balance(source_index, amount * 10), token_balance(destination_index, 0)],
            "postTokenBalances": [token_balance(source_index, amount * 9), token_balance(destination_index, amount)],
        }


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


@pytest.fixture
def quote() -> SettlementQuoteSnapshot:
    return SettlementQuoteSnapshot(
        source="fixed",
        version="1",
        commercial_amount="10.00",
        commercial_currency="USD",
    )


@pytest.fixture
def native_settlement(now: datetime, quote: SettlementQuoteSnapshot) -> SettlementSnapshot:
    _ = now
    from solders.pubkey import Pubkey

    return SettlementSnapshot(
        cluster="mainnet-beta",
        genesis_hash=MAINNET_GENESIS_HASH,
        asset_option_id="sol_native",
        kind=SolanaAssetKind.NATIVE,
        asset_name="Solana",
        asset_symbol="SOL",
        decimals=9,
        recipient_wallet=str(Pubkey.new_unique()),
        recipient_policy_version="test-treasury-v1",
        expected_raw_amount="25000000",
        display_amount="0.025",
        reference=str(Pubkey.new_unique()),
        required_commitment=SolanaCommitment.FINALIZED,
        quote=quote,
        memo="payment:test-native",
    )


@pytest.fixture
def token_settlement(quote: SettlementQuoteSnapshot) -> SettlementSnapshot:
    from solders.pubkey import Pubkey

    return SettlementSnapshot(
        cluster="mainnet-beta",
        genesis_hash=MAINNET_GENESIS_HASH,
        asset_option_id="solana_orcestr",
        kind=SolanaAssetKind.TOKEN,
        asset_name="Orcestr",
        asset_symbol="ORCESTR",
        mint=ORCESTR_TOKEN_MINT,
        token_program=TOKEN_2022_PROGRAM_ID,
        decimals=6,
        recipient_wallet=str(Pubkey.new_unique()),
        recipient_token_account=str(Pubkey.new_unique()),
        recipient_policy_version="test-treasury-v1",
        expected_raw_amount="1250000",
        display_amount="1.25",
        reference=str(Pubkey.new_unique()),
        required_commitment=SolanaCommitment.FINALIZED,
        quote=quote,
        memo="payment:test-token",
    )


class IssuanceFactory:
    """Creates issuance snapshots from an already compiled message."""

    @staticmethod
    def create(
        message: Any,
        payer: str,
        now: datetime,
        *,
        version: TransactionVersion = TransactionVersion.V0,
    ) -> TransactionIssuanceSnapshot:
        import hashlib

        return TransactionIssuanceSnapshot(
            public_id=uuid4(),
            payer=payer,
            recent_blockhash=str(message.recent_blockhash),
            last_valid_block_height=1000,
            issued_context_slot=800,
            message_sha256=hashlib.sha256(bytes(message)).hexdigest(),
            version=version,
            issued_at=now,
            accepts_until=now + timedelta(minutes=10),
        )
