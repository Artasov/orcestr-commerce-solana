from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orcestr_commerce_solana.clock import Clock, database_utc_datetime
from orcestr_commerce_solana.models import SolanaPaymentIntentORM, SolanaTransactionIssuanceORM
from orcestr_commerce_solana.repositories import SqlAlchemyUsedSignatureRegistry
from orcestr_commerce_solana.rpc.protocol import SolanaRpc
from orcestr_commerce_solana.schemas.assets import SettlementSnapshot
from orcestr_commerce_solana.schemas.transactions import TransactionIssuanceSnapshot, TransactionVersion
from orcestr_commerce_solana.services.settlement import SolanaSettlementService
from orcestr_commerce_solana.services.verification.contracts import (
    VerificationDisposition,
    VerificationRequest,
    VerificationResult,
)
from orcestr_commerce_solana.services.verification.verifier import SolanaTransactionVerifier


class SolanaCandidateProcessor:
    """Runs the connected-wallet fast path without trusting its candidate signature."""

    def __init__(
        self,
        rpc: SolanaRpc,
        clock: Clock,
        settlement: SolanaSettlementService,
        *,
        max_issuances_per_intent: int,
    ) -> None:
        if max_issuances_per_intent < 1:
            raise ValueError("Maximum issuances per intent must be positive.")
        self.rpc = rpc
        self.clock = clock
        self.settlement = settlement
        self.max_issuances_per_intent = max_issuances_per_intent

    async def process(
        self,
        session: AsyncSession,
        intent: SolanaPaymentIntentORM,
        signature: str,
        *,
        actor_key: str | None = None,
    ) -> VerificationResult | None:
        """Applies only a result tied to one persisted issuance message."""
        query = (
            select(SolanaTransactionIssuanceORM)
            .where(SolanaTransactionIssuanceORM.intent_id == intent.id)
            .order_by(SolanaTransactionIssuanceORM.issued_at.desc())
            .limit(self.max_issuances_per_intent)
        )
        issuances = tuple((await session.execute(query)).scalars())
        if not issuances:
            return None
        verifier = SolanaTransactionVerifier(self.rpc, SqlAlchemyUsedSignatureRegistry(session))
        prepared = await verifier.prepare(signature)
        settlement = SettlementSnapshot.model_validate(intent.settlement_snapshot)
        for issuance in issuances:
            result = await verifier.verify(
                VerificationRequest(
                    signature=signature,
                    intent_public_id=intent.public_id,
                    settlement=settlement,
                    issuance=TransactionIssuanceSnapshot(
                        public_id=issuance.public_id,
                        payer=issuance.payer,
                        recent_blockhash=issuance.recent_blockhash,
                        last_valid_block_height=issuance.last_valid_block_height,
                        issued_context_slot=issuance.issued_context_slot,
                        message_sha256=issuance.message_sha256,
                        version=TransactionVersion(issuance.transaction_version),
                        issued_at=database_utc_datetime(issuance.issued_at),
                        accepts_until=database_utc_datetime(issuance.accepts_until),
                    ),
                    now=self.clock.now(),
                    detection_source="candidate",
                ),
                prepared=prepared,
            )
            if result.reason_code == "issuance_mismatch":
                continue
            if result.disposition in {
                VerificationDisposition.CONFIRMED,
                VerificationDisposition.MATCH,
            }:
                await self.settlement.apply(session, intent, result, actor_key=actor_key)
            elif result.disposition in {VerificationDisposition.REVIEW, VerificationDisposition.FAILED}:
                await self.settlement.record_attempt(session, intent, result, actor_key=actor_key)
            return result
        return None
