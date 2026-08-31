from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from commercexl import PaymentState, PaymentVerificationResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orcestr_commerce_solana.integrations.commercexl import CommerceVerificationMapper
from orcestr_commerce_solana.clock import Clock, database_utc_datetime
from orcestr_commerce_solana.errors import SolanaErrorCode
from orcestr_commerce_solana.models import (
    SolanaIntentCapabilityORM,
    SolanaPaymentEventORM,
    SolanaPaymentIntentORM,
    SolanaTransactionIssuanceORM,
    SolanaTransferORM,
)
from orcestr_commerce_solana.schemas.intents import SolanaIntentState
from orcestr_commerce_solana.services.verification.contracts import VerificationDisposition, VerificationResult


class SolanaSettlementService:
    """Persists provider evidence and delegates product finalization to CommerceXL."""

    def __init__(self, payment_runtime: object, clock: Clock) -> None:
        self.payment_runtime = payment_runtime
        self.clock = clock

    async def apply(
        self,
        session: AsyncSession,
        intent: SolanaPaymentIntentORM,
        result: VerificationResult,
        *,
        actor_key: str | None = None,
    ) -> None:
        """Applies one verified result under the host transaction and core row locks."""
        if result.disposition == VerificationDisposition.UNKNOWN:
            return
        previous_state = intent.state
        next_state = self._intent_state(result)
        if self._is_repeated(intent, result, next_state):
            return
        now = self.clock.now()
        if result.transfer is not None:
            await self._record_transfer(session, intent, result, now)
            intent.verified_signature = result.transfer.signature
        intent.state = next_state.value
        intent.reason_code = result.reason_code
        if (
            result.disposition == VerificationDisposition.MATCH
            and now < database_utc_datetime(intent.reconcile_until)
        ):
            # Direct candidate verification also starts the bounded paid audit;
            # the reconciliation store replaces this with its retry delay.
            intent.next_check_at = now
        intent.revision += 1
        intent.updated_at = now
        if result.disposition in {
            VerificationDisposition.OBSERVED,
            VerificationDisposition.CONFIRMED,
            VerificationDisposition.MATCH,
        }:
            await self._revoke_capabilities(session, intent.id, now)
        session.add(
            SolanaPaymentEventORM(
                event_id=uuid4(),
                intent_id=intent.id,
                revision=intent.revision,
                event_type=f"solana.payment.{next_state.value}",
                previous_state=previous_state,
                next_state=next_state.value,
                reason_code=result.reason_code,
                evidence_sha256=result.transfer.evidence_sha256 if result.transfer else None,
                actor_key=actor_key,
                metadata_json={},
                occurred_at=now,
            ),
        )
        mapped = CommerceVerificationMapper.map(result)
        if mapped is not None:
            await self.payment_runtime.apply_verification(session, intent.payment_id, mapped)
            if mapped.state == PaymentState.PAID:
                intent.state = SolanaIntentState.PAID.value
        await session.flush()

    async def record_terminal_evidence(
        self,
        session: AsyncSession,
        intent: SolanaPaymentIntentORM,
        result: VerificationResult,
    ) -> bool:
        """Records a finalized transfer after a terminal state without granting a product."""
        if intent.state not in {SolanaIntentState.CANCELLED.value, SolanaIntentState.EXPIRED.value}:
            raise ValueError("Late payment evidence requires a terminal intent.")
        if result.transfer is None or result.transfer.commitment.value != "finalized":
            raise ValueError("Late payment evidence requires an exact finalized transfer.")
        if result.disposition not in {VerificationDisposition.MATCH, VerificationDisposition.REVIEW}:
            raise ValueError("Late payment evidence requires a matching or policy-review transfer.")
        if result.disposition == VerificationDisposition.REVIEW and result.reason_code not in {
            SolanaErrorCode.LATE_PAYMENT.value,
            SolanaErrorCode.BLOCK_TIME_UNAVAILABLE.value,
        }:
            raise ValueError("Terminal policy-review evidence must be an exact late transfer.")
        event_type = "solana.payment.late_payment"
        existing_event = (
            await session.execute(
                select(SolanaPaymentEventORM).where(
                    SolanaPaymentEventORM.intent_id == intent.id,
                    SolanaPaymentEventORM.event_type == event_type,
                    SolanaPaymentEventORM.evidence_sha256 == result.transfer.evidence_sha256,
                ),
            )
        ).scalar_one_or_none()
        if existing_event is not None:
            return False
        terminal_state = intent.state
        terminal_reason = result.reason_code or SolanaErrorCode.LATE_PAYMENT.value
        primary_signature = intent.verified_signature
        now = self.clock.now()
        await self._record_transfer(
            session,
            intent,
            result,
            now,
            reason_code=terminal_reason,
        )
        if intent.verified_signature is None:
            intent.verified_signature = result.transfer.signature
        intent.reason_code = terminal_reason
        intent.revision += 1
        intent.updated_at = now
        session.add(
            SolanaPaymentEventORM(
                event_id=uuid4(),
                intent_id=intent.id,
                revision=intent.revision,
                event_type=event_type,
                previous_state=terminal_state,
                next_state=terminal_state,
                reason_code=terminal_reason,
                evidence_sha256=result.transfer.evidence_sha256,
                actor_key=None,
                metadata_json={
                    "primary_signature": primary_signature or result.transfer.signature,
                    "product_effect": False,
                    "signature": result.transfer.signature,
                    "terminal_state": terminal_state,
                },
                occurred_at=now,
            ),
        )
        core_state = (
            PaymentState.CANCELLED
            if terminal_state == SolanaIntentState.CANCELLED.value
            else PaymentState.EXPIRED
        )
        await self.payment_runtime.apply_verification(
            session,
            intent.payment_id,
            PaymentVerificationResult(
                state=core_state,
                evidence_key=CommerceVerificationMapper.evidence_key(result.transfer),
                reason_code=terminal_reason,
                evidence=result.transfer.model_dump(mode="json"),
            ),
        )
        await session.flush()
        return True

    async def record_attempt(
        self,
        session: AsyncSession,
        intent: SolanaPaymentIntentORM,
        result: VerificationResult,
        *,
        actor_key: str | None = None,
    ) -> None:
        """Audits one exact failed or policy-review issuance without ending the intent."""
        if result.disposition not in {VerificationDisposition.REVIEW, VerificationDisposition.FAILED}:
            return
        transfer = result.transfer
        attempt = result.attempt
        evidence_sha256 = transfer.evidence_sha256 if transfer is not None else None
        signature = transfer.signature if transfer is not None else None
        issuance_public_id = transfer.issuance_public_id if transfer is not None else None
        if attempt is not None:
            evidence_sha256 = attempt.evidence_sha256
            signature = attempt.signature
            issuance_public_id = attempt.issuance_public_id
        if evidence_sha256 is None or signature is None or issuance_public_id is None:
            return
        event_type = f"solana.payment.attempt_{result.disposition.value}"
        existing = (
            await session.execute(
                select(SolanaPaymentEventORM).where(
                    SolanaPaymentEventORM.intent_id == intent.id,
                    SolanaPaymentEventORM.event_type == event_type,
                    SolanaPaymentEventORM.evidence_sha256 == evidence_sha256,
                ),
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        if transfer is not None:
            await self._record_transfer(
                session,
                intent,
                result,
                self.clock.now(),
                reason_code=result.reason_code,
            )
        now = self.clock.now()
        intent.revision += 1
        intent.updated_at = now
        session.add(
            SolanaPaymentEventORM(
                event_id=uuid4(),
                intent_id=intent.id,
                revision=intent.revision,
                event_type=event_type,
                previous_state=intent.state,
                next_state=intent.state,
                reason_code=result.reason_code,
                evidence_sha256=evidence_sha256,
                actor_key=actor_key,
                metadata_json={
                    "disposition": result.disposition.value,
                    "issuance_public_id": str(issuance_public_id),
                    "product_effect": False,
                    "signature": signature,
                },
                occurred_at=now,
            ),
        )
        await session.flush()

    async def record_duplicate_payment(
        self,
        session: AsyncSession,
        intent: SolanaPaymentIntentORM,
        result: VerificationResult,
    ) -> bool:
        """Audits a second finalized payment without calling the product runtime."""
        if intent.state != SolanaIntentState.PAID.value:
            raise ValueError("Duplicate payment evidence requires a paid intent.")
        transfer = result.transfer
        if (
            result.disposition != VerificationDisposition.MATCH
            or transfer is None
            or transfer.commitment.value != "finalized"
        ):
            raise ValueError("Duplicate payment evidence requires an exact finalized match.")
        if intent.verified_signature is None:
            raise ValueError("Paid Solana intent is missing its primary verified signature.")
        if transfer.signature == intent.verified_signature:
            return False
        event_type = "solana.payment.duplicate_payment"
        existing_event = (
            await session.execute(
                select(SolanaPaymentEventORM).where(
                    SolanaPaymentEventORM.intent_id == intent.id,
                    SolanaPaymentEventORM.event_type == event_type,
                    SolanaPaymentEventORM.evidence_sha256 == transfer.evidence_sha256,
                ),
            )
        ).scalar_one_or_none()
        if existing_event is not None:
            return False
        now = self.clock.now()
        await self._record_transfer(
            session,
            intent,
            result,
            now,
            reason_code="duplicate_payment",
        )
        intent.revision += 1
        intent.updated_at = now
        session.add(
            SolanaPaymentEventORM(
                event_id=uuid4(),
                intent_id=intent.id,
                revision=intent.revision,
                event_type=event_type,
                previous_state=SolanaIntentState.PAID.value,
                next_state=SolanaIntentState.PAID.value,
                reason_code="duplicate_payment",
                evidence_sha256=transfer.evidence_sha256,
                actor_key=None,
                metadata_json={
                    "primary_signature": intent.verified_signature,
                    "product_effect": False,
                    "signature": transfer.signature,
                },
                occurred_at=now,
            ),
        )
        await session.flush()
        return True

    async def expire(self, session: AsyncSession, intent: SolanaPaymentIntentORM) -> None:
        """Expires an unmatched intent only after the reconciler completed its final scan."""
        if intent.state not in {
            SolanaIntentState.WAITING.value,
            SolanaIntentState.OBSERVED.value,
            SolanaIntentState.CONFIRMED.value,
        }:
            return
        now = self.clock.now()
        previous_state = intent.state
        intent.state = SolanaIntentState.EXPIRED.value
        intent.reason_code = "intent_expired"
        intent.revision += 1
        intent.updated_at = now
        session.add(
            SolanaPaymentEventORM(
                event_id=uuid4(),
                intent_id=intent.id,
                revision=intent.revision,
                event_type="solana.payment.expired",
                previous_state=previous_state,
                next_state=SolanaIntentState.EXPIRED.value,
                reason_code="intent_expired",
                evidence_sha256=None,
                actor_key=None,
                metadata_json={"final_reference_scan": True},
                occurred_at=now,
            ),
        )
        await self.payment_runtime.apply_verification(
            session,
            intent.payment_id,
            PaymentVerificationResult(
                state=PaymentState.EXPIRED,
                reason_code="intent_expired",
            ),
        )
        await session.flush()

    async def quarantine(
        self,
        session: AsyncSession,
        intent: SolanaPaymentIntentORM,
        reason_code: str,
    ) -> None:
        """Parks a poisoned reference history without changing paid/terminal outcomes."""

        supported_states = {
            SolanaIntentState.WAITING.value,
            SolanaIntentState.OBSERVED.value,
            SolanaIntentState.CONFIRMED.value,
            SolanaIntentState.PAID.value,
            SolanaIntentState.CANCELLED.value,
            SolanaIntentState.EXPIRED.value,
        }
        if intent.state not in supported_states:
            return
        if intent.reason_code == reason_code and intent.next_check_at is None:
            return
        now = self.clock.now()
        previous_state = intent.state
        is_active = previous_state in {
            SolanaIntentState.WAITING.value,
            SolanaIntentState.OBSERVED.value,
            SolanaIntentState.CONFIRMED.value,
        }
        next_state = SolanaIntentState.REVIEW.value if is_active else previous_state
        intent.state = next_state
        intent.reason_code = reason_code
        intent.next_check_at = None
        intent.revision += 1
        intent.updated_at = now
        if is_active:
            await self._revoke_capabilities(session, intent.id, now)
        session.add(
            SolanaPaymentEventORM(
                event_id=uuid4(),
                intent_id=intent.id,
                revision=intent.revision,
                event_type="solana.payment.reference_scan_quarantined",
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                evidence_sha256=None,
                actor_key=None,
                metadata_json={"product_effect": False},
                occurred_at=now,
            )
        )
        if is_active:
            await self.payment_runtime.apply_verification(
                session,
                intent.payment_id,
                PaymentVerificationResult(
                    state=PaymentState.REVIEW,
                    reason_code=reason_code,
                ),
            )
        await session.flush()

    @staticmethod
    async def _revoke_capabilities(session: AsyncSession, intent_id: int, now: datetime) -> None:
        """Closes every bearer once any chain observation is applied."""
        capabilities = (
            await session.execute(
                select(SolanaIntentCapabilityORM)
                .where(
                    SolanaIntentCapabilityORM.intent_id == intent_id,
                    SolanaIntentCapabilityORM.revoked_at.is_(None),
                )
                .with_for_update(),
            )
        ).scalars()
        for capability in capabilities:
            capability.revoked_at = now

    @staticmethod
    def _is_repeated(
        intent: SolanaPaymentIntentORM,
        result: VerificationResult,
        next_state: SolanaIntentState,
    ) -> bool:
        """Prevents duplicate checks from creating events or product effects twice."""
        signature = result.transfer.signature if result.transfer else None
        if signature is not None and intent.verified_signature != signature:
            return False
        if result.disposition == VerificationDisposition.MATCH and intent.state == SolanaIntentState.PAID.value:
            return True
        return intent.state == next_state.value and intent.reason_code == result.reason_code

    async def _record_transfer(
        self,
        session: AsyncSession,
        intent: SolanaPaymentIntentORM,
        result: VerificationResult,
        now: datetime,
        *,
        reason_code: str | None = None,
    ) -> None:
        """Upserts commitment for the same transfer while preserving signature ownership."""
        transfer = result.transfer
        if transfer is None:
            return
        if transfer.cluster != intent.cluster:
            raise ValueError("Verified transfer cluster does not match the payment intent.")
        issuance = (
            await session.execute(
                select(SolanaTransactionIssuanceORM).where(
                    SolanaTransactionIssuanceORM.public_id == transfer.issuance_public_id,
                    SolanaTransactionIssuanceORM.intent_id == intent.id,
                ),
            )
        ).scalar_one_or_none()
        if issuance is None:
            raise ValueError("Verified transfer references an unknown transaction issuance.")
        query = (
            select(SolanaTransferORM)
            .where(
                SolanaTransferORM.cluster == intent.cluster,
                SolanaTransferORM.signature == transfer.signature,
            )
            .with_for_update()
        )
        existing = (await session.execute(query)).scalar_one_or_none()
        if existing is not None and existing.intent_id != intent.id:
            raise ValueError("Blockchain signature is already linked to another Solana intent.")
        if existing is not None and existing.issuance_id != issuance.id:
            raise ValueError("Blockchain signature is linked to a different transaction issuance.")
        if existing is None:
            session.add(
                SolanaTransferORM(
                    intent_id=intent.id,
                    issuance_id=issuance.id,
                    cluster=intent.cluster,
                    signature=transfer.signature,
                    instruction_index=transfer.instruction_index,
                    source_account=transfer.source_account,
                    destination_account=transfer.destination_account,
                    mint=transfer.mint,
                    token_program=transfer.token_program,
                    gross_raw_amount=int(transfer.gross_raw_amount),
                    net_raw_amount=int(transfer.net_raw_amount),
                    slot=transfer.slot,
                    block_time=transfer.block_time,
                    commitment=transfer.commitment.value,
                    transaction_version=transfer.transaction_version.value,
                    detection_source=transfer.detection_source,
                    verdict=result.disposition.value,
                    reason_code=reason_code or result.reason_code,
                    evidence_sha256=transfer.evidence_sha256,
                    created_at=now,
                    updated_at=now,
                ),
            )
            return
        existing.commitment = transfer.commitment.value
        existing.slot = transfer.slot
        existing.block_time = transfer.block_time
        existing.verdict = result.disposition.value
        existing.reason_code = result.reason_code
        if reason_code is not None:
            existing.reason_code = reason_code
        existing.updated_at = now

    @staticmethod
    def _intent_state(result: VerificationResult) -> SolanaIntentState:
        """Maps verifier disposition to a provider lifecycle state."""
        return {
            VerificationDisposition.OBSERVED: SolanaIntentState.OBSERVED,
            VerificationDisposition.CONFIRMED: SolanaIntentState.CONFIRMED,
            VerificationDisposition.MATCH: SolanaIntentState.PAID,
            VerificationDisposition.REVIEW: SolanaIntentState.REVIEW,
            VerificationDisposition.FAILED: SolanaIntentState.FAILED,
        }[result.disposition]
