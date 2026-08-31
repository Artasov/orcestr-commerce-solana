from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from commercexl import PaymentState

from orcestr_commerce_solana.integrations.fastapi import SolanaActor
from orcestr_commerce_solana.schemas.intents import CancelIntentRequest
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
