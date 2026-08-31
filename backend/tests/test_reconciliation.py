from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from solders.pubkey import Pubkey
from solders.signature import Signature

from conftest import FakeRpc
from orcestr_commerce_solana.errors import SolanaErrorCode, SolanaRpcUnavailableError
from orcestr_commerce_solana.rpc.types import RpcSignatureInfo
from orcestr_commerce_solana.schemas.assets import SettlementSnapshot, SolanaCommitment
from orcestr_commerce_solana.schemas.intents import SolanaIntentState
from orcestr_commerce_solana.schemas.transactions import TransactionIssuanceSnapshot, TransactionVersion
from orcestr_commerce_solana.services.reconciliation import ReconciliationIntent, SolanaReconciler
from orcestr_commerce_solana.services.verification.contracts import (
    VerificationAttemptEvidence,
    VerificationDisposition,
    VerificationResult,
    VerifiedTransfer,
)


class FakeReconciliationStore:
    """Captures state applications and retries for orchestration tests."""

    def __init__(self, intents: tuple[ReconciliationIntent, ...]) -> None:
        self.intents = intents
        self.applied: list[VerificationResult] = []
        self.retries: list[str | None] = []
        self.expired: list[object] = []
        self.attempts: list[VerificationResult] = []
        self.terminal_evidence: list[VerificationResult] = []
        self.finished_terminal: list[object] = []
        self.duplicate_payments: list[VerificationResult] = []
        self.finished_paid: list[object] = []
        self.quarantined: list[tuple[object, str]] = []

    async def list_pending(self, *, limit: int, now) -> tuple[ReconciliationIntent, ...]:
        _ = now
        return self.intents[:limit]

    async def apply(self, intent_public_id, result: VerificationResult) -> None:
        _ = intent_public_id
        self.applied.append(result)

    async def mark_retry(self, intent_public_id, reason_code: str | None) -> None:
        _ = intent_public_id
        self.retries.append(reason_code)

    async def expire(self, intent_public_id) -> None:
        self.expired.append(intent_public_id)

    async def record_attempt(self, intent_public_id, result: VerificationResult) -> None:
        _ = intent_public_id
        self.attempts.append(result)

    async def apply_terminal_evidence(self, intent_public_id, result: VerificationResult) -> bool:
        _ = intent_public_id
        self.terminal_evidence.append(result)
        return True

    async def finish_terminal(self, intent_public_id) -> None:
        self.finished_terminal.append(intent_public_id)

    async def record_duplicate_payment(self, intent_public_id, result: VerificationResult) -> bool:
        _ = intent_public_id
        self.duplicate_payments.append(result)
        return True

    async def finish_paid_audit(self, intent_public_id) -> None:
        self.finished_paid.append(intent_public_id)

    async def quarantine(self, intent_public_id, reason_code: str) -> None:
        self.quarantined.append((intent_public_id, reason_code))


class FakeVerifier:
    """Returns deterministic results by untrusted candidate signature."""

    def __init__(self, results: dict[str, VerificationResult]) -> None:
        self.results = results
        self.seen: list[str] = []
        self.prepared: list[str] = []

    async def prepare(self, signature: str) -> str:
        self.prepared.append(signature)
        return signature

    async def verify(self, request, *, prepared=None) -> VerificationResult:
        assert prepared == request.signature
        self.seen.append(request.signature)
        return self.results[request.signature]


class IssuanceAwareFakeVerifier(FakeVerifier):
    """Returns a match only for the issuance referenced by frozen transfer evidence."""

    async def verify(self, request, *, prepared=None) -> VerificationResult:
        assert prepared == request.signature
        self.seen.append(request.signature)
        result = self.results[request.signature]
        if (
            result.transfer is not None
            and result.transfer.issuance_public_id != request.issuance.public_id
        ):
            return VerificationResult(
                disposition=VerificationDisposition.REVIEW,
                reason_code="issuance_mismatch",
            )
        return result


class OutageHistoryRpc(FakeRpc):
    """Fails only the reference-history scan at the expiry boundary."""

    async def get_signatures_for_address(self, *args, **kwargs):
        _ = args
        _ = kwargs
        raise SolanaRpcUnavailableError(
            SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE,
            "fixture outage",
        )


class PagedHistoryRpc(FakeRpc):
    """Returns deterministic pages keyed by the preceding page cursor."""

    def __init__(self, pages: dict[str | None, tuple[RpcSignatureInfo, ...]]) -> None:
        super().__init__()
        self.pages = pages
        self.seen_before: list[str | None] = []

    async def get_signatures_for_address(self, address, *, before=None, limit=100, commitment=None):
        _ = address
        _ = commitment
        self.seen_before.append(before)
        return self.pages.get(before, ())[:limit]


class TestSolanaReconciler:
    """Covers false candidate isolation and provisional finality application."""

    @pytest.mark.asyncio
    async def test_false_candidate_cannot_block_later_valid_signature(
        self,
        now,
        native_settlement: SettlementSnapshot,
    ) -> None:
        false_signature = str(Signature.new_unique())
        valid_signature = str(Signature.new_unique())
        issuance = self._issuance(now)
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            candidate_signatures=(false_signature, valid_signature),
            expires_at=now + timedelta(minutes=10),
            reconcile_until=now + timedelta(minutes=15),
        )
        match = self._transfer_result(valid_signature, native_settlement, now, VerificationDisposition.MATCH)
        verifier = FakeVerifier(
            {
                false_signature: VerificationResult(
                    disposition=VerificationDisposition.REVIEW,
                    reason_code="issuance_mismatch",
                ),
                valid_signature: match,
            },
        )
        store = FakeReconciliationStore((intent,))

        stats = await SolanaReconciler(FakeRpc(), verifier, store).reconcile_pending(limit=10, now=now)

        assert verifier.seen == [false_signature, valid_signature]
        assert store.applied == [match]
        assert store.retries == []
        assert stats.applied == 1

    @pytest.mark.asyncio
    async def test_linked_failed_candidate_does_not_block_later_valid_signature(
        self,
        now,
        native_settlement: SettlementSnapshot,
    ) -> None:
        failed_signature = str(Signature.new_unique())
        valid_signature = str(Signature.new_unique())
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(self._issuance(now),),
            candidate_signatures=(failed_signature, valid_signature),
            expires_at=now + timedelta(minutes=10),
            reconcile_until=now + timedelta(minutes=15),
        )
        failed = VerificationResult(
            disposition=VerificationDisposition.FAILED,
            reason_code="transaction_failed",
        )
        match = self._transfer_result(valid_signature, native_settlement, now, VerificationDisposition.MATCH)
        verifier = FakeVerifier({failed_signature: failed, valid_signature: match})
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(FakeRpc(), verifier, store).reconcile_pending(limit=10, now=now)

        assert verifier.seen == [failed_signature, valid_signature]
        assert store.applied == [match]

    @pytest.mark.asyncio
    async def test_confirmed_is_applied_and_scheduled_for_finalized_recheck(
        self,
        now,
        native_settlement: SettlementSnapshot,
    ) -> None:
        signature = str(Signature.new_unique())
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(self._issuance(now),),
            candidate_signatures=(signature,),
            expires_at=now + timedelta(minutes=10),
            reconcile_until=now + timedelta(minutes=15),
        )
        confirmed = self._transfer_result(signature, native_settlement, now, VerificationDisposition.CONFIRMED)
        confirmed = confirmed.model_copy(update={"retryable": True, "reason_code": "transaction_not_final"})
        store = FakeReconciliationStore((intent,))

        stats = await SolanaReconciler(
            FakeRpc(),
            FakeVerifier({signature: confirmed}),
            store,
        ).reconcile_pending(limit=10, now=now)

        assert store.applied == [confirmed]
        assert store.retries == ["transaction_not_final"]
        assert stats.applied == 1
        assert stats.retryable == 1

    @pytest.mark.asyncio
    async def test_rpc_outage_at_expiry_never_guesses_expired(self, now, native_settlement) -> None:
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(self._issuance(now - timedelta(minutes=20)),),
            expires_at=now - timedelta(minutes=1),
            reconcile_until=now - timedelta(seconds=1),
        )
        store = FakeReconciliationStore((intent,))

        stats = await SolanaReconciler(OutageHistoryRpc(), FakeVerifier({}), store).reconcile_pending(
            limit=10,
            now=now,
        )

        assert store.expired == []
        assert store.retries == ["rpc_temporarily_unavailable"]
        assert stats.retryable == 1

    @pytest.mark.asyncio
    async def test_untrusted_unknown_candidate_does_not_block_complete_expiry_scan(
        self,
        now,
        native_settlement,
    ) -> None:
        signature = str(Signature.new_unique())
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(self._issuance(now - timedelta(minutes=20)),),
            candidate_signatures=(signature,),
            expires_at=now - timedelta(seconds=1),
            reconcile_until=now + timedelta(minutes=4),
        )
        unknown = VerificationResult(
            disposition=VerificationDisposition.UNKNOWN,
            reason_code="transaction_not_found",
            retryable=True,
        )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(
            FakeRpc(),
            FakeVerifier({signature: unknown}),
            store,
        ).reconcile_pending(limit=10, now=now)

        assert store.expired == [intent.public_id]
        assert store.retries == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("disposition", (VerificationDisposition.UNKNOWN, VerificationDisposition.OBSERVED))
    async def test_unverified_reference_result_does_not_extend_public_expiry(
        self,
        now,
        native_settlement,
        disposition,
    ) -> None:
        signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=20))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now - timedelta(seconds=1),
            reconcile_until=now + timedelta(minutes=4),
        )
        rpc = FakeRpc()
        rpc.history = (RpcSignatureInfo(signature=signature, slot=10, block_time=now),)
        result = VerificationResult(
            disposition=disposition,
            reason_code=(
                "transaction_not_found"
                if disposition == VerificationDisposition.UNKNOWN
                else "transaction_not_final"
            ),
            retryable=True,
        )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(
            rpc,
            FakeVerifier({signature: result}),
            store,
        ).reconcile_pending(limit=10, now=now)

        if disposition == VerificationDisposition.UNKNOWN:
            assert store.expired == []
            assert store.retries == ["transaction_not_found"]
        else:
            assert store.expired == [intent.public_id]
            assert store.retries == []
        assert store.terminal_evidence == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "provisional_disposition",
        (VerificationDisposition.OBSERVED, VerificationDisposition.CONFIRMED),
    )
    async def test_unknown_cannot_be_masked_when_expiry_is_terminal(
        self,
        now,
        native_settlement,
        provisional_disposition,
    ) -> None:
        unknown_signature = str(Signature.new_unique())
        provisional_signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=20))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now - timedelta(minutes=5),
            reconcile_until=now - timedelta(seconds=1),
        )
        rpc = FakeRpc()
        rpc.history = (
            RpcSignatureInfo(signature=unknown_signature, slot=11, block_time=now),
            RpcSignatureInfo(signature=provisional_signature, slot=10, block_time=now),
        )
        unknown = VerificationResult(
            disposition=VerificationDisposition.UNKNOWN,
            reason_code="rpc_temporarily_unavailable",
            retryable=True,
        )
        provisional = (
            self._transfer_result(
                provisional_signature,
                native_settlement,
                now,
                VerificationDisposition.CONFIRMED,
            ).model_copy(update={"retryable": True, "reason_code": "transaction_not_final"})
            if provisional_disposition == VerificationDisposition.CONFIRMED
            else VerificationResult(
                disposition=VerificationDisposition.OBSERVED,
                reason_code="transaction_not_final",
                retryable=True,
            )
        )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(
            rpc,
            FakeVerifier(
                {
                    unknown_signature: unknown,
                    provisional_signature: provisional,
                }
            ),
            store,
        ).reconcile_pending(limit=10, now=now)

        assert store.expired == []
        assert store.finished_terminal == []
        assert store.retries == ["rpc_temporarily_unavailable"]

    @pytest.mark.asyncio
    async def test_exact_match_settles_after_expiry_even_when_another_candidate_is_unknown(
        self,
        now,
        native_settlement,
    ) -> None:
        unknown_signature = str(Signature.new_unique())
        match_signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=20))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now - timedelta(seconds=1),
            reconcile_until=now + timedelta(minutes=4),
        )
        rpc = FakeRpc()
        rpc.history = (
            RpcSignatureInfo(signature=unknown_signature, slot=11, block_time=now),
            RpcSignatureInfo(signature=match_signature, slot=10, block_time=now),
        )
        unknown = VerificationResult(
            disposition=VerificationDisposition.UNKNOWN,
            reason_code="rpc_temporarily_unavailable",
            retryable=True,
        )
        match = self._transfer_result(
            match_signature,
            native_settlement,
            issuance.accepts_until - timedelta(seconds=1),
            VerificationDisposition.MATCH,
        )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(
            rpc,
            FakeVerifier({unknown_signature: unknown, match_signature: match}),
            store,
        ).reconcile_pending(limit=10, now=now)

        assert store.applied == [match]
        assert store.expired == []
        assert store.retries == []

    @pytest.mark.asyncio
    async def test_unknown_does_not_consume_late_evidence_before_terminal_retry(
        self,
        now,
        native_settlement,
    ) -> None:
        late_signature = str(Signature.new_unique())
        unknown_signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=20))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now - timedelta(seconds=1),
            reconcile_until=now + timedelta(minutes=4),
        )
        late_match = self._transfer_result(
            late_signature,
            native_settlement,
            now,
            VerificationDisposition.MATCH,
        )
        late_review = VerificationResult(
            disposition=VerificationDisposition.REVIEW,
            reason_code=SolanaErrorCode.LATE_PAYMENT.value,
            commitment=SolanaCommitment.FINALIZED,
            transfer=late_match.transfer,
        )
        unknown = VerificationResult(
            disposition=VerificationDisposition.UNKNOWN,
            reason_code="rpc_temporarily_unavailable",
            retryable=True,
        )
        rpc = FakeRpc()
        rpc.history = (
            RpcSignatureInfo(signature=late_signature, slot=11, block_time=now),
            RpcSignatureInfo(signature=unknown_signature, slot=10, block_time=now),
        )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(
            rpc,
            FakeVerifier({late_signature: late_review, unknown_signature: unknown}),
            store,
        ).reconcile_pending(limit=10, now=now)

        assert store.attempts == []
        assert store.expired == []
        assert store.retries == ["rpc_temporarily_unavailable"]

        rpc.history = (RpcSignatureInfo(signature=late_signature, slot=11, block_time=now),)
        await SolanaReconciler(
            rpc,
            FakeVerifier({late_signature: late_review}),
            store,
        ).reconcile_pending(limit=10, now=now)

        assert store.expired == [intent.public_id]
        assert store.terminal_evidence == [late_review]

    @pytest.mark.asyncio
    async def test_on_time_confirmed_payment_waits_for_finality_after_public_expiry(
        self,
        now,
        native_settlement,
    ) -> None:
        signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=20))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now - timedelta(seconds=1),
            reconcile_until=now + timedelta(minutes=4),
        )
        rpc = FakeRpc()
        rpc.history = (
            RpcSignatureInfo(
                signature=signature,
                slot=10,
                block_time=issuance.accepts_until - timedelta(seconds=1),
            ),
        )
        confirmed = self._transfer_result(
            signature,
            native_settlement,
            issuance.accepts_until - timedelta(seconds=1),
            VerificationDisposition.CONFIRMED,
        ).model_copy(
            update={"retryable": True, "reason_code": "transaction_not_final"},
        )
        store = FakeReconciliationStore((intent,))

        stats = await SolanaReconciler(
            rpc,
            FakeVerifier({signature: confirmed}),
            store,
        ).reconcile_pending(limit=10, now=now)

        assert store.applied == [confirmed]
        assert store.expired == []
        assert store.retries == ["transaction_not_final"]
        assert stats.applied == 1
        assert stats.retryable == 1

    @pytest.mark.asyncio
    async def test_confirmed_payment_expires_at_reconciliation_horizon(
        self,
        now,
        native_settlement,
    ) -> None:
        signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=20))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now - timedelta(minutes=5),
            reconcile_until=now - timedelta(seconds=1),
        )
        rpc = FakeRpc()
        rpc.history = (
            RpcSignatureInfo(
                signature=signature,
                slot=10,
                block_time=issuance.accepts_until - timedelta(seconds=1),
            ),
        )
        confirmed = self._transfer_result(
            signature,
            native_settlement,
            issuance.accepts_until - timedelta(seconds=1),
            VerificationDisposition.CONFIRMED,
        ).model_copy(
            update={"retryable": True, "reason_code": "transaction_not_final"},
        )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(
            rpc,
            FakeVerifier({signature: confirmed}),
            store,
        ).reconcile_pending(limit=10, now=now)

        assert store.applied == []
        assert store.expired == [intent.public_id]
        assert store.retries == []

    @pytest.mark.asyncio
    async def test_failed_issuance_audit_does_not_block_valid_second_pass(
        self,
        now,
        native_settlement,
    ) -> None:
        failed_signature = str(Signature.new_unique())
        valid_signature = str(Signature.new_unique())
        issuance = self._issuance(now)
        base = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            candidate_signatures=(failed_signature,),
            expires_at=now + timedelta(minutes=10),
            reconcile_until=now + timedelta(minutes=15),
        )
        failed = VerificationResult(
            disposition=VerificationDisposition.FAILED,
            reason_code="transaction_failed",
            attempt=VerificationAttemptEvidence(
                signature=failed_signature,
                issuance_public_id=issuance.public_id,
                evidence_sha256="8" * 64,
            ),
        )
        first_store = FakeReconciliationStore((base,))
        await SolanaReconciler(
            FakeRpc(),
            FakeVerifier({failed_signature: failed}),
            first_store,
        ).reconcile_pending(limit=10, now=now)

        match = self._transfer_result(valid_signature, native_settlement, now, VerificationDisposition.MATCH)
        second_store = FakeReconciliationStore(
            (base.model_copy(update={"candidate_signatures": (valid_signature,)}),),
        )
        await SolanaReconciler(
            FakeRpc(),
            FakeVerifier({valid_signature: match}),
            second_store,
        ).reconcile_pending(limit=10, now=now + timedelta(seconds=5))

        assert first_store.applied == []
        assert first_store.attempts == [failed]
        assert first_store.retries == [None]
        assert second_store.applied == [match]

    @pytest.mark.asyncio
    async def test_signature_rpc_material_is_prepared_once_for_many_issuances(
        self,
        now,
        native_settlement,
    ) -> None:
        signature = str(Signature.new_unique())
        issuances = tuple(self._issuance(now) for _ in range(16))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=issuances,
            candidate_signatures=(signature,),
            expires_at=now + timedelta(minutes=10),
            reconcile_until=now + timedelta(minutes=15),
        )
        mismatch = VerificationResult(
            disposition=VerificationDisposition.REVIEW,
            reason_code="issuance_mismatch",
        )
        verifier = FakeVerifier({signature: mismatch})

        await SolanaReconciler(
            FakeRpc(),
            verifier,
            FakeReconciliationStore((intent,)),
        ).reconcile_pending(limit=10, now=now)

        assert verifier.prepared == [signature]
        assert verifier.seen == [signature] * len(issuances)

    @pytest.mark.asyncio
    async def test_terminal_late_review_is_evidence_but_other_review_is_only_audit(
        self,
        now,
        native_settlement,
    ) -> None:
        late_signature = str(Signature.new_unique())
        duplicate_signature = str(Signature.new_unique())
        intent = ReconciliationIntent(
            public_id=uuid4(),
            state=SolanaIntentState.CANCELLED,
            settlement=native_settlement,
            issuances=(self._issuance(now - timedelta(minutes=20)),),
            expires_at=now - timedelta(minutes=5),
            reconcile_until=now + timedelta(minutes=1),
        )
        late = self._transfer_result(
            late_signature,
            native_settlement,
            now,
            VerificationDisposition.MATCH,
        ).model_copy(
            update={
                "disposition": VerificationDisposition.REVIEW,
                "reason_code": "late_payment",
            },
        )
        duplicate = self._transfer_result(
            duplicate_signature,
            native_settlement,
            now,
            VerificationDisposition.MATCH,
        ).model_copy(
            update={
                "disposition": VerificationDisposition.REVIEW,
                "reason_code": "signature_already_used",
            },
        )
        rpc = FakeRpc()
        rpc.history = (
            RpcSignatureInfo(signature=duplicate_signature, slot=10, block_time=now),
            RpcSignatureInfo(signature=late_signature, slot=9, block_time=now),
        )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(
            rpc,
            FakeVerifier({duplicate_signature: duplicate, late_signature: late}),
            store,
        ).reconcile_pending(limit=10, now=now)

        assert store.terminal_evidence == [late]
        assert store.attempts == [duplicate]
        assert store.retries == [None]

    @pytest.mark.asyncio
    async def test_terminal_scan_records_every_new_late_payment_without_starvation(
        self,
        now,
        native_settlement,
    ) -> None:
        recorded_signature = str(Signature.new_unique())
        first_signature = str(Signature.new_unique())
        second_signature = str(Signature.new_unique())
        first_issuance = self._issuance(now - timedelta(minutes=20))
        second_issuance = self._issuance(now - timedelta(minutes=19))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            state=SolanaIntentState.EXPIRED,
            settlement=native_settlement,
            issuances=(first_issuance, second_issuance),
            verified_signature=recorded_signature,
            recorded_signatures=(recorded_signature,),
            expires_at=now - timedelta(minutes=5),
            reconcile_until=now + timedelta(minutes=1),
        )
        first = self._transfer_result(
            first_signature,
            native_settlement,
            now,
            VerificationDisposition.MATCH,
        )
        first = first.model_copy(
            update={
                "transfer": first.transfer.model_copy(
                    update={"issuance_public_id": first_issuance.public_id},
                ),
            },
        )
        second = self._transfer_result(
            second_signature,
            native_settlement,
            now,
            VerificationDisposition.MATCH,
        )
        second = second.model_copy(
            update={
                "transfer": second.transfer.model_copy(
                    update={"issuance_public_id": second_issuance.public_id},
                ),
            },
        )
        rpc = FakeRpc()
        rpc.history = (
            RpcSignatureInfo(signature=recorded_signature, slot=12, block_time=now),
            RpcSignatureInfo(signature=first_signature, slot=11, block_time=now),
            RpcSignatureInfo(signature=second_signature, slot=10, block_time=now),
        )
        verifier = IssuanceAwareFakeVerifier(
            {first_signature: first, second_signature: second},
        )
        store = FakeReconciliationStore((intent,))

        stats = await SolanaReconciler(rpc, verifier, store).reconcile_pending(limit=10, now=now)

        assert verifier.prepared == [first_signature, second_signature]
        assert store.terminal_evidence == [first, second]
        assert store.retries == [None]
        assert store.finished_terminal == []
        assert stats.applied == 2

    @pytest.mark.asyncio
    async def test_terminal_scan_retries_until_horizon_then_finishes(self, now, native_settlement) -> None:
        base = ReconciliationIntent(
            public_id=uuid4(),
            state=SolanaIntentState.EXPIRED,
            settlement=native_settlement,
            issuances=(self._issuance(now - timedelta(minutes=20)),),
            expires_at=now - timedelta(minutes=5),
            reconcile_until=now + timedelta(seconds=30),
        )
        before_store = FakeReconciliationStore((base,))
        await SolanaReconciler(FakeRpc(), FakeVerifier({}), before_store).reconcile_pending(
            limit=10,
            now=now,
        )
        after_store = FakeReconciliationStore(
            (base.model_copy(update={"reconcile_until": now - timedelta(seconds=1)}),),
        )
        await SolanaReconciler(FakeRpc(), FakeVerifier({}), after_store).reconcile_pending(
            limit=10,
            now=now,
        )

        assert before_store.retries == [None]
        assert before_store.finished_terminal == []
        assert after_store.retries == []
        assert after_store.finished_terminal == [base.public_id]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "disposition",
        (
            VerificationDisposition.UNKNOWN,
            VerificationDisposition.OBSERVED,
            VerificationDisposition.CONFIRMED,
        ),
    )
    async def test_terminal_horizon_finishes_despite_provisional_reference_result(
        self,
        now,
        native_settlement,
        disposition,
    ) -> None:
        signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=20))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            state=SolanaIntentState.EXPIRED,
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now - timedelta(minutes=5),
            reconcile_until=now - timedelta(seconds=1),
        )
        rpc = FakeRpc()
        rpc.history = (RpcSignatureInfo(signature=signature, slot=10, block_time=now),)
        if disposition == VerificationDisposition.CONFIRMED:
            result = self._transfer_result(signature, native_settlement, now, disposition)
            result = result.model_copy(update={"retryable": True, "reason_code": "transaction_not_final"})
        else:
            result = VerificationResult(
                disposition=disposition,
                reason_code="transaction_not_final",
                retryable=True,
            )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(
            rpc,
            FakeVerifier({signature: result}),
            store,
        ).reconcile_pending(limit=10, now=now)

        if disposition == VerificationDisposition.UNKNOWN:
            assert store.finished_terminal == []
            assert store.retries == ["transaction_not_final"]
        else:
            assert store.finished_terminal == [intent.public_id]
            assert store.retries == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("state", "finished_attribute"),
        (
            (SolanaIntentState.EXPIRED, "finished_terminal"),
            (SolanaIntentState.PAID, "finished_paid"),
        ),
    )
    async def test_unknown_cannot_be_masked_when_finishing_existing_audit(
        self,
        now,
        native_settlement,
        state,
        finished_attribute,
    ) -> None:
        unknown_signature = str(Signature.new_unique())
        confirmed_signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=20))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            state=state,
            settlement=native_settlement,
            issuances=(issuance,),
            verified_signature=(
                str(Signature.new_unique())
                if state == SolanaIntentState.PAID
                else None
            ),
            expires_at=now - timedelta(minutes=5),
            reconcile_until=now - timedelta(seconds=1),
        )
        rpc = FakeRpc()
        rpc.history = (
            RpcSignatureInfo(signature=unknown_signature, slot=11, block_time=now),
            RpcSignatureInfo(signature=confirmed_signature, slot=10, block_time=now),
        )
        unknown = VerificationResult(
            disposition=VerificationDisposition.UNKNOWN,
            reason_code="rpc_temporarily_unavailable",
            retryable=True,
        )
        confirmed = self._transfer_result(
            confirmed_signature,
            native_settlement,
            now,
            VerificationDisposition.CONFIRMED,
        ).model_copy(update={"retryable": True, "reason_code": "transaction_not_final"})
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(
            rpc,
            FakeVerifier(
                {
                    unknown_signature: unknown,
                    confirmed_signature: confirmed,
                }
            ),
            store,
        ).reconcile_pending(limit=10, now=now)

        assert getattr(store, finished_attribute) == []
        assert store.retries == ["rpc_temporarily_unavailable"]

    @pytest.mark.asyncio
    async def test_paid_audit_records_second_finalized_payment_without_reapplying_primary(
        self,
        now,
        native_settlement,
    ) -> None:
        primary_signature = str(Signature.new_unique())
        duplicate_signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=1))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            state=SolanaIntentState.PAID,
            settlement=native_settlement,
            issuances=(issuance,),
            verified_signature=primary_signature,
            expires_at=now + timedelta(minutes=9),
            reconcile_until=now + timedelta(minutes=14),
        )
        rpc = FakeRpc()
        rpc.history = (
            RpcSignatureInfo(signature=primary_signature, slot=11, block_time=now),
            RpcSignatureInfo(signature=duplicate_signature, slot=10, block_time=now),
        )
        duplicate = self._transfer_result(
            duplicate_signature,
            native_settlement,
            now,
            VerificationDisposition.MATCH,
        )
        verifier = FakeVerifier({duplicate_signature: duplicate})
        store = FakeReconciliationStore((intent,))

        stats = await SolanaReconciler(rpc, verifier, store).reconcile_pending(limit=10, now=now)

        assert verifier.seen == [duplicate_signature]
        assert store.applied == []
        assert store.duplicate_payments == [duplicate]
        assert store.retries == [None]
        assert stats.applied == 1

    @pytest.mark.asyncio
    async def test_paid_audit_stops_after_complete_horizon_scan(self, now, native_settlement) -> None:
        intent = ReconciliationIntent(
            public_id=uuid4(),
            state=SolanaIntentState.PAID,
            settlement=native_settlement,
            issuances=(self._issuance(now - timedelta(minutes=20)),),
            verified_signature=str(Signature.new_unique()),
            expires_at=now - timedelta(minutes=5),
            reconcile_until=now - timedelta(seconds=1),
        )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(FakeRpc(), FakeVerifier({}), store).reconcile_pending(
            limit=10,
            now=now,
        )

        assert store.finished_paid == [intent.public_id]
        assert store.retries == []

    @pytest.mark.asyncio
    async def test_successful_empty_final_scan_expires_unmatched_intent(self, now, native_settlement) -> None:
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(self._issuance(now - timedelta(minutes=20)),),
            expires_at=now - timedelta(minutes=1),
            reconcile_until=now - timedelta(seconds=1),
        )
        store = FakeReconciliationStore((intent,))

        stats = await SolanaReconciler(FakeRpc(), FakeVerifier({}), store).reconcile_pending(limit=10, now=now)

        assert store.expired == [intent.public_id]
        assert store.retries == []
        assert stats.expired == 1

    @pytest.mark.asyncio
    async def test_on_time_finalized_payment_discovered_after_public_expiry_settles(
        self,
        now,
        native_settlement,
    ) -> None:
        signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=20))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now - timedelta(minutes=1),
            reconcile_until=now + timedelta(minutes=4),
        )
        rpc = FakeRpc()
        rpc.history = (
            RpcSignatureInfo(signature=signature, slot=10, block_time=issuance.accepts_until - timedelta(seconds=1)),
        )
        match = self._transfer_result(
            signature,
            native_settlement,
            issuance.accepts_until - timedelta(seconds=1),
            VerificationDisposition.MATCH,
        )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(rpc, FakeVerifier({signature: match}), store).reconcile_pending(limit=10, now=now)

        assert store.applied == [match]
        assert store.expired == []
        assert store.terminal_evidence == []

    @pytest.mark.asyncio
    async def test_on_time_finalized_payment_discovered_after_horizon_is_terminal_evidence(
        self,
        now,
        native_settlement,
    ) -> None:
        signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=20))
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now - timedelta(minutes=5),
            reconcile_until=now,
        )
        rpc = FakeRpc()
        rpc.history = (
            RpcSignatureInfo(
                signature=signature,
                slot=10,
                block_time=issuance.accepts_until - timedelta(seconds=1),
            ),
        )
        match = self._transfer_result(
            signature,
            native_settlement,
            issuance.accepts_until - timedelta(seconds=1),
            VerificationDisposition.MATCH,
        )
        store = FakeReconciliationStore((intent,))

        stats = await SolanaReconciler(
            rpc,
            FakeVerifier({signature: match}),
            store,
        ).reconcile_pending(limit=10, now=now)

        assert store.applied == []
        assert store.expired == [intent.public_id]
        assert store.terminal_evidence == [match]
        assert store.finished_terminal == [intent.public_id]
        assert stats.applied == 1
        assert stats.expired == 1

    @pytest.mark.asyncio
    async def test_reference_overflow_is_quarantined_after_one_bounded_window(
        self,
        now,
        native_settlement,
    ) -> None:
        spam = [str(Signature.new_unique()) for _ in range(17)]
        issuance = self._issuance(now - timedelta(minutes=5))
        rpc = PagedHistoryRpc(
            {
                None: tuple(
                    RpcSignatureInfo(signature=signature, slot=50 - index, block_time=now)
                    for index, signature in enumerate(spam)
                )
            }
        )
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now - timedelta(minutes=1),
            reconcile_until=now + timedelta(minutes=4),
        )
        mismatch = VerificationResult(
            disposition=VerificationDisposition.REVIEW,
            reason_code="issuance_mismatch",
        )
        verifier = FakeVerifier({signature: mismatch for signature in spam})
        store = FakeReconciliationStore((intent,))

        stats = await SolanaReconciler(
            rpc,
            verifier,
            store,
        ).reconcile_pending(limit=10, now=now)

        assert rpc.seen_before == [None]
        assert verifier.prepared == spam[:16]
        assert store.quarantined == [
            (intent.public_id, "reference_candidate_budget_exceeded")
        ]
        assert store.expired == []
        assert store.terminal_evidence == []
        assert stats.quarantined == 1

    @pytest.mark.asyncio
    async def test_direct_exact_match_has_priority_over_reference_overflow(
        self,
        now,
        native_settlement,
    ) -> None:
        valid = str(Signature.new_unique())
        spam = [str(Signature.new_unique()) for _ in range(17)]
        issuance = self._issuance(now - timedelta(minutes=5))
        rpc = PagedHistoryRpc(
            {
                None: tuple(
                    RpcSignatureInfo(signature=signature, slot=50 - index, block_time=now)
                    for index, signature in enumerate(spam)
                ),
            },
        )
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            candidate_signatures=(valid,),
            expires_at=now + timedelta(minutes=1),
            reconcile_until=now + timedelta(minutes=4),
        )
        mismatch = VerificationResult(
            disposition=VerificationDisposition.REVIEW,
            reason_code="issuance_mismatch",
        )
        match = self._transfer_result(
            valid,
            native_settlement,
            issuance.accepts_until - timedelta(seconds=1),
            VerificationDisposition.MATCH,
        )
        store = FakeReconciliationStore((intent,))

        verifier = FakeVerifier({valid: match, **{signature: mismatch for signature in spam}})
        stats = await SolanaReconciler(
            rpc,
            verifier,
            store,
        ).reconcile_pending(limit=10, now=now)

        assert verifier.prepared == [valid]
        assert store.applied == [match]
        assert store.expired == []
        assert store.retries == []
        assert store.quarantined == []
        assert stats.applied == 1

    @pytest.mark.asyncio
    async def test_unknown_inside_overflow_retries_without_quarantine(
        self,
        now,
        native_settlement,
    ) -> None:
        signatures = [str(Signature.new_unique()) for _ in range(17)]
        issuance = self._issuance(now - timedelta(minutes=5))
        rpc = FakeRpc()
        rpc.history = tuple(
            RpcSignatureInfo(signature=signature, slot=50 - index, block_time=now)
            for index, signature in enumerate(signatures)
        )
        unknown = VerificationResult(
            disposition=VerificationDisposition.UNKNOWN,
            reason_code="rpc_temporarily_unavailable",
            retryable=True,
        )
        mismatch = VerificationResult(
            disposition=VerificationDisposition.REVIEW,
            reason_code="issuance_mismatch",
        )
        verifier = FakeVerifier(
            {signatures[0]: unknown, **{signature: mismatch for signature in signatures[1:]}},
        )
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now + timedelta(minutes=1),
            reconcile_until=now + timedelta(minutes=4),
        )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(rpc, verifier, store).reconcile_pending(limit=10, now=now)

        assert len(verifier.prepared) == 16
        assert store.retries == ["rpc_temporarily_unavailable"]
        assert store.quarantined == []
        assert store.expired == []

    @pytest.mark.asyncio
    async def test_exact_budget_with_lower_slot_can_expire_deterministically(
        self,
        now,
        native_settlement,
    ) -> None:
        signatures = [str(Signature.new_unique()) for _ in range(16)]
        old_signature = str(Signature.new_unique())
        issuance = self._issuance(now - timedelta(minutes=20))
        rpc = FakeRpc()
        rpc.history = (
            *tuple(
                RpcSignatureInfo(signature=signature, slot=50 - index, block_time=now)
                for index, signature in enumerate(signatures)
            ),
            RpcSignatureInfo(signature=old_signature, slot=4, block_time=now),
        )
        mismatch = VerificationResult(
            disposition=VerificationDisposition.REVIEW,
            reason_code="issuance_mismatch",
        )
        verifier = FakeVerifier({signature: mismatch for signature in signatures})
        intent = ReconciliationIntent(
            public_id=uuid4(),
            settlement=native_settlement,
            issuances=(issuance,),
            expires_at=now - timedelta(seconds=1),
            reconcile_until=now + timedelta(minutes=4),
        )
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(rpc, verifier, store).reconcile_pending(limit=10, now=now)

        assert verifier.prepared == signatures
        assert store.expired == [intent.public_id]
        assert store.quarantined == []

    @pytest.mark.asyncio
    async def test_global_prepare_budget_defers_whole_remaining_intents(
        self,
        now,
        native_settlement,
    ) -> None:
        signature_groups = [
            (str(Signature.new_unique()), str(Signature.new_unique()))
            for _ in range(3)
        ]
        intents = tuple(
            ReconciliationIntent(
                public_id=uuid4(),
                settlement=native_settlement,
                issuances=(self._issuance(now),),
                candidate_signatures=signatures,
                expires_at=now + timedelta(minutes=1),
                reconcile_until=now + timedelta(minutes=4),
            )
            for signatures in signature_groups
        )
        mismatch = VerificationResult(
            disposition=VerificationDisposition.REVIEW,
            reason_code="issuance_mismatch",
        )
        verifier = FakeVerifier(
            {
                signature: mismatch
                for signatures in signature_groups
                for signature in signatures
            }
        )
        store = FakeReconciliationStore(intents)

        await SolanaReconciler(
            FakeRpc(),
            verifier,
            store,
            max_candidate_verifications_per_intent=2,
            max_candidate_verifications_per_pass=4,
        ).reconcile_pending(limit=10, now=now)

        assert verifier.prepared == [*signature_groups[0], *signature_groups[1]]
        assert store.retries == [
            None,
            None,
            "candidate_verification_global_budget_exhausted",
        ]
        assert store.quarantined == []

    @pytest.mark.asyncio
    async def test_candidate_also_found_by_reference_consumes_one_prepare(
        self,
        now,
        native_settlement,
    ) -> None:
        signature = str(Signature.new_unique())
        issuance = self._issuance(now)
        intent = ReconciliationIntent(
            public_id=uuid4(),
            state=SolanaIntentState.EXPIRED,
            settlement=native_settlement,
            issuances=(issuance,),
            candidate_signatures=(signature,),
            expires_at=now - timedelta(minutes=1),
            reconcile_until=now + timedelta(minutes=4),
        )
        rpc = FakeRpc()
        rpc.history = (RpcSignatureInfo(signature=signature, slot=10, block_time=now),)
        match = self._transfer_result(
            signature,
            native_settlement,
            now,
            VerificationDisposition.MATCH,
        )
        verifier = FakeVerifier({signature: match})
        store = FakeReconciliationStore((intent,))

        await SolanaReconciler(rpc, verifier, store).reconcile_pending(limit=10, now=now)

        assert verifier.prepared == [signature]
        assert store.terminal_evidence == [match]

    @staticmethod
    def _issuance(now) -> TransactionIssuanceSnapshot:
        return TransactionIssuanceSnapshot(
            public_id=uuid4(),
            payer=str(Pubkey.new_unique()),
            recent_blockhash=str(Pubkey.new_unique()),
            last_valid_block_height=100,
            issued_context_slot=5,
            message_sha256="0" * 64,
            version=TransactionVersion.V0,
            issued_at=now,
            accepts_until=now + timedelta(minutes=10),
        )

    @staticmethod
    def _transfer_result(
        signature: str,
        settlement: SettlementSnapshot,
        now,
        disposition: VerificationDisposition,
    ) -> VerificationResult:
        return VerificationResult(
            disposition=disposition,
            commitment=(
                SolanaCommitment.FINALIZED
                if disposition == VerificationDisposition.MATCH
                else SolanaCommitment.CONFIRMED
            ),
            transfer=VerifiedTransfer(
                cluster=settlement.cluster,
                signature=signature,
                issuance_public_id=uuid4(),
                instruction_index=1,
                source_account=str(Pubkey.new_unique()),
                destination_account=settlement.recipient_wallet,
                gross_raw_amount=settlement.expected_raw_amount,
                net_raw_amount=settlement.expected_raw_amount,
                slot=1,
                block_time=now,
                commitment=(
                    SolanaCommitment.FINALIZED
                    if disposition == VerificationDisposition.MATCH
                    else SolanaCommitment.CONFIRMED
                ),
                transaction_version=TransactionVersion.V0,
                detection_source="candidate",
                evidence_sha256="1" * 64,
            ),
        )
