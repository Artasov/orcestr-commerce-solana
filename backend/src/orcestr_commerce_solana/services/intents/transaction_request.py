from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from orcestr_commerce_solana.clock import Clock
from orcestr_commerce_solana.config import SolanaCommerceConfig
from orcestr_commerce_solana.errors import SolanaCommerceError, SolanaErrorCode
from orcestr_commerce_solana.models import SolanaTransactionIssuanceORM
from orcestr_commerce_solana.schemas.transactions import (
    SolanaPayGetResponse,
    SolanaPayPostRequest,
    SolanaPayPostResponse,
    TransactionBuildRequest,
)
from orcestr_commerce_solana.schemas.assets import SolanaAssetKind
from orcestr_commerce_solana.services.intents.builder import SolanaTransactionBuilder

if TYPE_CHECKING:
    from orcestr_commerce_solana.repositories import SolanaIntentRepository


class SolanaTransactionRequestService:
    """Resolves capabilities and atomically records each unsigned issuance."""

    def __init__(
        self,
        config: SolanaCommerceConfig,
        clock: Clock,
        intents: SolanaIntentRepository,
        builder: SolanaTransactionBuilder,
        *,
        icon: str | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.intents = intents
        self.builder = builder
        self.icon = icon

    async def get(self, session: AsyncSession, capability: str) -> SolanaPayGetResponse:
        """Returns protocol metadata only after resolving an active digest."""
        resolved = await self.intents.get_by_capability(session, capability)
        self._check_kind(resolved.settlement.kind)
        return SolanaPayGetResponse(label=self.config.merchant_label, icon=self.icon)

    async def post(
        self,
        session: AsyncSession,
        capability: str,
        request: SolanaPayPostRequest,
    ) -> SolanaPayPostResponse:
        """Builds one exact transaction and persists its issuance before commit."""
        resolved = await self.intents.get_by_capability(session, capability)
        self._check_kind(resolved.settlement.kind)
        issuance_count = await self.intents.count_issuances(session, resolved.intent_id)
        if issuance_count >= self.config.max_issuances_per_intent:
            raise SolanaCommerceError(
                SolanaErrorCode.ISSUANCE_LIMIT_REACHED,
                "This payment intent has reached its transaction issuance limit.",
            )
        now = self.clock.now()
        accepts_until = min(resolved.expires_at, resolved.capability_expires_at) - self.config.issuance_safety_margin
        if accepts_until <= now:
            raise SolanaCommerceError(SolanaErrorCode.CAPABILITY_EXPIRED, "Transaction request is no longer safe to issue.")
        result = await self.builder.build(
            TransactionBuildRequest(
                issuance_public_id=uuid4(),
                payer=request.account,
                settlement=resolved.settlement,
                issued_at=now,
                accepts_until=accepts_until,
            ),
        )
        issuance = result.issuance
        session.add(
            SolanaTransactionIssuanceORM(
                public_id=issuance.public_id,
                intent_id=resolved.intent_id,
                capability_id=resolved.capability_id,
                payer=issuance.payer,
                recent_blockhash=issuance.recent_blockhash,
                last_valid_block_height=issuance.last_valid_block_height,
                issued_context_slot=issuance.issued_context_slot,
                message_sha256=issuance.message_sha256,
                transaction_version=issuance.version.value,
                issued_at=issuance.issued_at,
                accepts_until=issuance.accepts_until,
            ),
        )
        await session.flush()
        return SolanaPayPostResponse(transaction=result.transaction, message=result.message)

    def _check_kind(self, kind: SolanaAssetKind) -> None:
        """Applies host kill switches to public capability endpoints."""
        if (kind == SolanaAssetKind.NATIVE and not self.config.enable_native_sol) or (
            kind == SolanaAssetKind.TOKEN and not self.config.enable_token_2022
        ):
            raise SolanaCommerceError(SolanaErrorCode.ASSET_INACTIVE, "Solana asset kind is disabled.")
