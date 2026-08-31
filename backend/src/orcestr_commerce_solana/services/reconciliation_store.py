from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from orcestr_commerce_solana.clock import Clock, database_utc_datetime
from orcestr_commerce_solana.config import SolanaCommerceConfig
from orcestr_commerce_solana.models import (
    SolanaPaymentEventORM,
    SolanaPaymentIntentORM,
    SolanaTransactionIssuanceORM,
    SolanaTransferORM,
)
from orcestr_commerce_solana.schemas.assets import SettlementSnapshot
from orcestr_commerce_solana.schemas.intents import SolanaIntentState
from orcestr_commerce_solana.schemas.transactions import TransactionIssuanceSnapshot, TransactionVersion
from orcestr_commerce_solana.repositories import SessionFactoryUsedSignatureRegistry
from orcestr_commerce_solana.rpc.protocol import SolanaRpc
from orcestr_commerce_solana.services.reconciliation import ReconciliationIntent, SolanaReconciler
from orcestr_commerce_solana.services.settlement import SolanaSettlementService
from orcestr_commerce_solana.services.verification.contracts import (
    VerificationDisposition,
    VerificationResult,
)
from orcestr_commerce_solana.services.verification.verifier import SolanaTransactionVerifier


class ReconciliationSessionFactory(Protocol):
    """Creates one host-owned session per atomic reconciliation operation."""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Returns an async session context."""
        ...


class SqlAlchemyReconciliationStore:
    """Claims due intents and applies verifier results in short DB transactions."""

    active_states = (
        SolanaIntentState.WAITING.value,
        SolanaIntentState.OBSERVED.value,
        SolanaIntentState.CONFIRMED.value,
    )
    terminal_states = (
        SolanaIntentState.CANCELLED.value,
        SolanaIntentState.EXPIRED.value,
    )
    paid_audit_state = SolanaIntentState.PAID.value

    def __init__(
        self,
        session_factory: ReconciliationSessionFactory,
        settlement: SolanaSettlementService,
        clock: Clock,
        *,
        max_issuances_per_intent: int,
        retry_delay: timedelta = timedelta(seconds=5),
        claim_lease: timedelta = timedelta(seconds=30),
    ) -> None:
        if retry_delay <= timedelta(0):
            raise ValueError("Reconciliation retry delay must be positive.")
        if claim_lease <= timedelta(0):
            raise ValueError("Reconciliation claim lease must be positive.")
        if max_issuances_per_intent < 1:
            raise ValueError("Maximum issuances per intent must be positive.")
        self.session_factory = session_factory
        self.settlement = settlement
        self.clock = clock
        self.retry_delay = retry_delay
        self.claim_lease = claim_lease
        self.max_issuances_per_intent = max_issuances_per_intent

    async def list_pending(self, *, limit: int, now) -> tuple[ReconciliationIntent, ...]:
        """Atomically leases one due batch so concurrent workers do not duplicate RPC work."""
        if limit < 1:
            raise ValueError("Reconciliation batch limit must be positive.")
        async with self.session_factory() as session, session.begin():
            query = (
                select(SolanaPaymentIntentORM)
                .where(
                    or_(
                        and_(
                            SolanaPaymentIntentORM.state.in_(self.active_states),
                            or_(
                                SolanaPaymentIntentORM.next_check_at.is_(None),
                                SolanaPaymentIntentORM.next_check_at <= now,
                            ),
                        ),
                        and_(
                            SolanaPaymentIntentORM.state.in_(self.terminal_states),
                            SolanaPaymentIntentORM.next_check_at.is_not(None),
                            SolanaPaymentIntentORM.next_check_at <= now,
                        ),
                        and_(
                            SolanaPaymentIntentORM.state == self.paid_audit_state,
                            SolanaPaymentIntentORM.next_check_at.is_not(None),
                            SolanaPaymentIntentORM.next_check_at <= now,
                        ),
                    ),
                )
                .order_by(SolanaPaymentIntentORM.next_check_at, SolanaPaymentIntentORM.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            intents = tuple((await session.execute(query)).scalars())
            if not intents:
                return ()
            leased_until = now + self.claim_lease
            snapshots: list[ReconciliationIntent] = []
            for intent in intents:
                intent.next_check_at = leased_until
                issuance_query = (
                    select(SolanaTransactionIssuanceORM)
                    .where(SolanaTransactionIssuanceORM.intent_id == intent.id)
                    .order_by(SolanaTransactionIssuanceORM.issued_at.desc())
                    .limit(self.max_issuances_per_intent)
                )
                issuances = tuple((await session.execute(issuance_query)).scalars())
                recorded_signatures = tuple(
                    (
                        await session.execute(
                            select(SolanaTransferORM.signature)
                            .where(SolanaTransferORM.intent_id == intent.id)
                            .order_by(SolanaTransferORM.id)
                            .limit(self.max_issuances_per_intent)
                        )
                    ).scalars()
                )
                snapshots.append(
                    ReconciliationIntent(
                        public_id=intent.public_id,
                        state=SolanaIntentState(intent.state),
                        settlement=SettlementSnapshot.model_validate(intent.settlement_snapshot),
                        issuances=tuple(self._issuance_snapshot(issuance) for issuance in issuances),
                        candidate_signatures=(intent.candidate_signature,) if intent.candidate_signature else (),
                        verified_signature=intent.verified_signature,
                        recorded_signatures=recorded_signatures,
                        expires_at=database_utc_datetime(intent.expires_at),
                        reconcile_until=database_utc_datetime(intent.reconcile_until),
                    ),
                )
            await session.flush()
            return tuple(snapshots)

    async def apply(self, intent_public_id: UUID, result: VerificationResult) -> None:
        """Locks one intent and applies transfer evidence plus CommerceXL state atomically."""
        async with self.session_factory() as session, session.begin():
            intent = await self._get_locked(session, intent_public_id)
            if intent is None or intent.state not in self.active_states:
                return
            await self.settlement.apply(session, intent, result)
            if result.disposition == VerificationDisposition.MATCH:
                now = self.clock.now()
                reconcile_until = database_utc_datetime(intent.reconcile_until)
                intent.next_check_at = (
                    min(now + self.retry_delay, reconcile_until)
                    if now < reconcile_until
                    else None
                )
            elif result.disposition in {VerificationDisposition.REVIEW, VerificationDisposition.FAILED}:
                intent.next_check_at = None

    async def mark_retry(self, intent_public_id: UUID, reason_code: str | None) -> None:
        """Schedules another pass without persisting infrastructure uncertainty as state."""
        _ = reason_code
        async with self.session_factory() as session, session.begin():
            intent = await self._get_locked(session, intent_public_id)
            if intent is None or intent.state not in {
                *self.active_states,
                *self.terminal_states,
                self.paid_audit_state,
            }:
                return
            if await self._is_reference_quarantined(session, intent.id):
                return
            intent.next_check_at = self.clock.now() + self.retry_delay
            intent.updated_at = self.clock.now()

    async def apply_terminal_evidence(self, intent_public_id: UUID, result: VerificationResult) -> bool:
        """Records a new late finalized transfer and leaves CommerceXL terminal."""
        async with self.session_factory() as session, session.begin():
            intent = await self._get_locked(session, intent_public_id)
            if intent is None or intent.state not in self.terminal_states:
                return False
            if await self._is_reference_quarantined(session, intent.id):
                return False
            recorded = await self.settlement.record_terminal_evidence(session, intent, result)
            now = self.clock.now()
            reconcile_until = database_utc_datetime(intent.reconcile_until)
            intent.next_check_at = (
                min(now + self.retry_delay, reconcile_until)
                if now < reconcile_until
                else now + self.retry_delay
            )
            return recorded

    async def finish_terminal(self, intent_public_id: UUID) -> None:
        """Marks a successful no-match terminal scan as complete."""
        async with self.session_factory() as session, session.begin():
            intent = await self._get_locked(session, intent_public_id)
            if intent is None or intent.state not in self.terminal_states:
                return
            intent.next_check_at = None
            intent.updated_at = self.clock.now()

    async def record_attempt(self, intent_public_id: UUID, result: VerificationResult) -> None:
        """Writes bounded per-issuance evidence while preserving the current lifecycle state."""
        async with self.session_factory() as session, session.begin():
            intent = await self._get_locked(session, intent_public_id)
            if intent is None or intent.state not in {
                *self.active_states,
                *self.terminal_states,
                self.paid_audit_state,
            }:
                return
            await self.settlement.record_attempt(session, intent, result)

    async def record_duplicate_payment(self, intent_public_id: UUID, result: VerificationResult) -> bool:
        """Writes one additional finalized transfer while leaving paid state untouched."""
        async with self.session_factory() as session, session.begin():
            intent = await self._get_locked(session, intent_public_id)
            if intent is None or intent.state != self.paid_audit_state:
                return False
            return await self.settlement.record_duplicate_payment(session, intent, result)

    async def finish_paid_audit(self, intent_public_id: UUID) -> None:
        """Stops duplicate-payment scans after a complete horizon pass."""
        async with self.session_factory() as session, session.begin():
            intent = await self._get_locked(session, intent_public_id)
            if intent is None or intent.state != self.paid_audit_state:
                return
            intent.next_check_at = None
            intent.updated_at = self.clock.now()

    async def quarantine(self, intent_public_id: UUID, reason_code: str) -> None:
        """Persists one durable manual-review boundary for reference-history overflow."""

        async with self.session_factory() as session, session.begin():
            intent = await self._get_locked(session, intent_public_id)
            if intent is None:
                return
            await self.settlement.quarantine(session, intent, reason_code)

    async def expire(self, intent_public_id: UUID) -> None:
        """Expires only unmatched states; confirmed evidence must reach another verdict."""
        async with self.session_factory() as session, session.begin():
            intent = await self._get_locked(session, intent_public_id)
            if intent is None or intent.state not in {
                SolanaIntentState.WAITING.value,
                SolanaIntentState.OBSERVED.value,
                SolanaIntentState.CONFIRMED.value,
            }:
                return
            issuance_count = int(
                await session.scalar(
                    select(func.count(SolanaTransactionIssuanceORM.id)).where(
                        SolanaTransactionIssuanceORM.intent_id == intent.id,
                    ),
                )
                or 0
            )
            await self.settlement.expire(session, intent)
            now = self.clock.now()
            reconcile_until = database_utc_datetime(intent.reconcile_until)
            intent.next_check_at = (
                (
                    min(now + self.retry_delay, reconcile_until)
                    if now < reconcile_until
                    else now + self.retry_delay
                )
                if issuance_count > 0
                else None
            )

    @staticmethod
    async def _get_locked(session: AsyncSession, intent_public_id: UUID) -> SolanaPaymentIntentORM | None:
        query = (
            select(SolanaPaymentIntentORM)
            .where(SolanaPaymentIntentORM.public_id == intent_public_id)
            .with_for_update()
        )
        return (await session.execute(query)).scalar_one_or_none()

    @staticmethod
    async def _is_reference_quarantined(session: AsyncSession, intent_id: int) -> bool:
        """Uses the append-only event as a durable stale-worker fence."""

        event_id = await session.scalar(
            select(SolanaPaymentEventORM.id)
            .where(
                SolanaPaymentEventORM.intent_id == intent_id,
                SolanaPaymentEventORM.event_type
                == "solana.payment.reference_scan_quarantined",
            )
            .limit(1)
        )
        return event_id is not None

    @staticmethod
    def _issuance_snapshot(issuance: SolanaTransactionIssuanceORM) -> TransactionIssuanceSnapshot:
        return TransactionIssuanceSnapshot(
            public_id=issuance.public_id,
            payer=issuance.payer,
            recent_blockhash=issuance.recent_blockhash,
            last_valid_block_height=issuance.last_valid_block_height,
            issued_context_slot=issuance.issued_context_slot,
            message_sha256=issuance.message_sha256,
            version=TransactionVersion(issuance.transaction_version),
            issued_at=database_utc_datetime(issuance.issued_at),
            accepts_until=database_utc_datetime(issuance.accepts_until),
        )


def create_sqlalchemy_reconciler(
    *,
    config: SolanaCommerceConfig,
    rpc: SolanaRpc,
    clock: Clock,
    session_factory: ReconciliationSessionFactory,
    payment_runtime: object,
    retry_delay: timedelta = timedelta(seconds=5),
    claim_lease: timedelta = timedelta(seconds=30),
) -> SolanaReconciler:
    """Wires the production DB registry/store so hosts do not duplicate orchestration."""
    settlement = SolanaSettlementService(payment_runtime, clock)
    store = SqlAlchemyReconciliationStore(
        session_factory,
        settlement,
        clock,
        max_issuances_per_intent=config.max_issuances_per_intent,
        retry_delay=retry_delay,
        claim_lease=claim_lease,
    )
    verifier = SolanaTransactionVerifier(rpc, SessionFactoryUsedSignatureRegistry(session_factory))
    return SolanaReconciler(
        rpc,
        verifier,
        store,
        max_candidate_verifications_per_intent=(
            config.max_candidate_verifications_per_intent
        ),
        max_candidate_verifications_per_pass=(
            config.max_candidate_verifications_per_pass
        ),
    )
