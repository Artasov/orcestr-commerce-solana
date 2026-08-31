from __future__ import annotations

from typing import Protocol
from uuid import UUID

from orcestr_commerce_solana.rpc.types import (
    RpcAccountInfo,
    RpcLatestBlockhash,
    RpcSignatureInfo,
    RpcSignatureStatus,
    RpcTransaction,
)
from orcestr_commerce_solana.schemas.assets import SolanaCommitment


class SolanaRpc(Protocol):
    """Defines the standard JSON-RPC surface required for free verification."""

    async def check_cluster(self) -> str:
        """Checks endpoints against the configured genesis hash."""
        ...

    async def get_account_info(self, address: str, commitment: SolanaCommitment) -> RpcAccountInfo | None:
        """Reads one account in base64 form."""
        ...

    async def get_latest_blockhash(self, commitment: SolanaCommitment) -> RpcLatestBlockhash:
        """Reads a recent blockhash and last valid block height."""
        ...

    async def get_block_height(self, commitment: SolanaCommitment) -> int:
        """Reads the current block height for issuance cutoffs."""
        ...

    async def get_signature_status(self, signature: str) -> RpcSignatureStatus | None:
        """Reads a signature status while searching transaction history."""
        ...

    async def get_transaction(self, signature: str, commitment: SolanaCommitment) -> RpcTransaction | None:
        """Reads a raw base64 transaction and its execution metadata."""
        ...

    async def get_signatures_for_address(
        self,
        address: str,
        *,
        before: str | None = None,
        limit: int = 100,
        commitment: SolanaCommitment = SolanaCommitment.CONFIRMED,
    ) -> tuple[RpcSignatureInfo, ...]:
        """Finds candidate signatures by the unique reference account."""
        ...


class UsedSignatureRegistry(Protocol):
    """Checks the host database before a signature can settle an intent."""

    async def is_used(self, cluster: str, signature: str, intent_public_id: UUID) -> bool:
        """Returns whether another persisted payment already claimed the signature."""
        ...
