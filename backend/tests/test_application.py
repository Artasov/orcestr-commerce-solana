from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from commercexl import PaymentState

from orcestr_commerce_solana.integrations.fastapi import SolanaActor
from solders.signature import Signature

from orcestr_commerce_solana.schemas.intents import (
    CancelIntentRequest,
    CandidateSignatureRequest,
)
from orcestr_commerce_solana.services.application import SolanaApplicationService


class FakeSession:
    """Provides the nested context shape consumed by the application service."""

    @asynccontextmanager
    async def begin(self):
        yield


class OldPaymentRuntime:
    """Models an old terminal payment while a newer order attempt remains active."""

    def __init__(self, state: PaymentState) -> None:
        self.payment = SimpleNamespace(
            id=uuid4(),
            order_id=uuid4(),
            state=state,
        )
        self.newer_active_cancelled = False

    async def get_payment_status(self, session, payment_public_id, actor):
        _ = session
        _ = payment_public_id
        _ = actor
        return self.payment

    async def cancel_for_order(self, session, order_id, actor):
        _ = session
        _ = order_id
        _ = actor
        self.newer_active_cancelled = True

    @staticmethod
    def get_conflict(message: str) -> RuntimeError:
        return RuntimeError(message)


class FailIfTouchedIntents:
    """Proves a terminal target is rejected before provider mutation."""

    async def record_cancel_request(self, *args, **kwargs):
        _ = args
        _ = kwargs
        raise AssertionError("Terminal payment must not persist a cancellation command.")


class TestSolanaApplicationCancellation:
    """Prevents an old payment URL from cancelling a newer active order attempt."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "terminal_state",
        [PaymentState.EXPIRED, PaymentState.FAILED, PaymentState.PAID, PaymentState.REFUNDED],
    )
    async def test_old_terminal_payment_never_cancels_newer_active_attempt(self, terminal_state) -> None:
        runtime = OldPaymentRuntime(terminal_state)

        @asynccontextmanager
        async def session_factory():
            yield FakeSession()

        service = SolanaApplicationService(
            session_factory,
            runtime,
            FailIfTouchedIntents(),
            SimpleNamespace(),
            SimpleNamespace(),
        )

        with pytest.raises(RuntimeError, match="selected payment attempt"):
            await service.cancel_intent(
                runtime.payment.id,
                CancelIntentRequest(reason="user_cancelled", idempotency_key="cancel-old-payment"),
                SolanaActor(actor_key="user:1", user_id=1),
            )

        assert runtime.newer_active_cancelled is False


class TestSolanaApplicationCandidates:
    """Pins duplicate idempotency and the commit-before-RPC boundary."""

    @pytest.mark.asyncio
    async def test_new_candidate_prepares_only_after_claim_transaction_closes(
        self,
        now,
        native_settlement,
    ) -> None:
        events: list[str] = []
        signature = str(Signature.new_unique())
        payment_id = uuid4()
        payment = SimpleNamespace(
            id=payment_id,
            order_id=uuid4(),
            state=PaymentState.REQUIRES_ACTION,
            action=None,
            reason_code=None,
            revision=1,
            updated_at=now,
        )
        intent = SimpleNamespace(
            public_id=uuid4(),
            settlement_snapshot=native_settlement.model_dump(mode="json"),
            candidate_signature=signature,
            verified_signature=None,
            reason_code=None,
            expires_at=now + timedelta(minutes=5),
            created_at=now,
            updated_at=now,
        )

        @asynccontextmanager
        async def session_factory():
            events.append("session_open")
            try:
                yield FakeSession()
            finally:
                events.append("session_close")

        class Runtime:
            async def get_payment_status(self, session, public_id, actor):
                _ = session
                _ = public_id
                _ = actor
                return payment

        class Intents:
            async def claim_candidate(self, session, public_id, value, *, actor_key):
                _ = session
                _ = public_id
                _ = value
                _ = actor_key
                events.append("claim")
                return SimpleNamespace(is_new=True, has_issuances=True)

            async def get_by_payment_public_id(self, session, public_id, *, for_update=False):
                _ = session
                _ = public_id
                assert for_update is True
                return intent

        class Candidates:
            async def prepare(self, value):
                _ = value
                events.append("prepare")
                return "prepared"

            async def process(self, session, current_intent, value, *, prepared, actor_key):
                _ = session
                _ = current_intent
                _ = value
                _ = actor_key
                assert prepared == "prepared"
                events.append("process")

        service = SolanaApplicationService(
            session_factory,
            Runtime(),
            Intents(),
            Candidates(),
            SimpleNamespace(),
        )

        await service.submit_candidate(
            payment_id,
            CandidateSignatureRequest(signature=signature),
            SolanaActor(actor_key="user:1", user_id=1),
        )

        assert events == [
            "session_open",
            "claim",
            "session_close",
            "prepare",
            "session_open",
            "process",
            "session_close",
        ]

    @pytest.mark.asyncio
    async def test_duplicate_candidate_performs_no_rpc(
        self,
        now,
        native_settlement,
    ) -> None:
        signature = str(Signature.new_unique())
        payment_id = uuid4()
        payment = SimpleNamespace(
            id=payment_id,
            order_id=uuid4(),
            state=PaymentState.REQUIRES_ACTION,
            action=None,
            reason_code=None,
            revision=1,
            updated_at=now,
        )
        intent = SimpleNamespace(
            public_id=uuid4(),
            settlement_snapshot=native_settlement.model_dump(mode="json"),
            candidate_signature=signature,
            verified_signature=None,
            reason_code=None,
            expires_at=now + timedelta(minutes=5),
            created_at=now,
            updated_at=now,
        )

        @asynccontextmanager
        async def session_factory():
            yield FakeSession()

        class Runtime:
            async def get_payment_status(self, session, public_id, actor):
                _ = session
                _ = public_id
                _ = actor
                return payment

        class Intents:
            async def claim_candidate(self, session, public_id, value, *, actor_key):
                _ = session
                _ = public_id
                _ = value
                _ = actor_key
                return SimpleNamespace(is_new=False, has_issuances=False)

            async def get_by_payment_public_id(self, session, public_id, *, for_update=False):
                _ = session
                _ = public_id
                assert for_update is False
                return intent

        class Candidates:
            async def prepare(self, value):
                _ = value
                raise AssertionError("Duplicate candidate must not call Solana RPC.")

            async def process(self, *args, **kwargs):
                _ = args
                _ = kwargs
                raise AssertionError("Duplicate candidate must not run verification.")

        service = SolanaApplicationService(
            session_factory,
            Runtime(),
            Intents(),
            Candidates(),
            SimpleNamespace(),
        )

        result = await service.submit_candidate(
            payment_id,
            CandidateSignatureRequest(signature=signature),
            SolanaActor(actor_key="user:1", user_id=1),
        )

        assert result.candidate_signature == signature
