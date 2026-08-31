from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from solders.pubkey import Pubkey

from orcestr_commerce_solana.errors import SolanaCommerceError, SolanaErrorCode
from orcestr_commerce_solana.integrations.fastapi import (
    SolanaActor,
    SolanaFastApiConfig,
    SolanaFastApiRouterFactory,
)
from orcestr_commerce_solana.schemas.intents import (
    SolanaActionKind,
    SolanaCheckoutAction,
    SolanaIntentDTO,
    SolanaIntentState,
    SolanaPaymentOptionsResponse,
)
from orcestr_commerce_solana.schemas.transactions import SolanaPayGetResponse, SolanaPayPostResponse


VALID_CAPABILITY = "A" * 43


class FakeAccessPolicy:
    """Captures host ownership checks performed before service calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID, str]] = []

    async def check_order(self, actor: SolanaActor, order_public_id: UUID, action: str) -> None:
        self.calls.append((actor.actor_key, order_public_id, action))

    async def check_payment(self, actor: SolanaActor, payment_public_id: UUID, action: str) -> None:
        self.calls.append((actor.actor_key, payment_public_id, action))


class FakeApiService:
    """Returns typed values while keeping router tests independent of persistence."""

    def __init__(self, intent: SolanaIntentDTO) -> None:
        self.intent = intent

    async def list_payment_options(self, order_public_id, actor) -> SolanaPaymentOptionsResponse:
        _ = order_public_id
        _ = actor
        return SolanaPaymentOptionsResponse(options=())

    async def create_intent(self, request, actor) -> SolanaIntentDTO:
        _ = request
        _ = actor
        return self.intent

    async def get_intent(self, payment_public_id, actor) -> SolanaIntentDTO:
        _ = payment_public_id
        _ = actor
        return self.intent

    async def issue_action(self, payment_public_id, actor) -> SolanaCheckoutAction:
        _ = payment_public_id
        _ = actor
        return self.intent.action

    async def submit_candidate(self, payment_public_id, request, actor) -> SolanaIntentDTO:
        _ = payment_public_id
        _ = request
        _ = actor
        return self.intent

    async def cancel_intent(self, payment_public_id, request, actor) -> SolanaIntentDTO:
        _ = payment_public_id
        _ = request
        _ = actor
        return self.intent

    async def get_transaction_request(self, capability: str) -> SolanaPayGetResponse:
        _ = capability
        return SolanaPayGetResponse(label="Orcestr")

    async def create_transaction_request(self, capability, request) -> SolanaPayPostResponse:
        _ = capability
        _ = request
        return SolanaPayPostResponse(transaction="dHJhbnNhY3Rpb24=", message="Review payment")


class FailingCapabilityService(FakeApiService):
    """Raises one typed package error from both public capability methods."""

    def __init__(self, intent: SolanaIntentDTO, error: SolanaCommerceError) -> None:
        super().__init__(intent)
        self.error = error

    async def get_transaction_request(self, capability: str) -> SolanaPayGetResponse:
        _ = capability
        raise self.error

    async def create_transaction_request(self, capability, request) -> SolanaPayPostResponse:
        _ = capability
        _ = request
        raise self.error


class TestSolanaFastApiRouter:
    """Covers typed auth/CSRF ports and public capability cache headers."""

    def test_public_transaction_request_is_no_store(self, native_settlement, now) -> None:
        client, _, _ = self._client(native_settlement, now)

        response = client.get(f"/commerce/solana/transaction-requests/{VALID_CAPABILITY}")

        assert response.status_code == 200, response.json()
        assert response.json() == {"label": "Orcestr", "icon": None}
        assert response.headers["cache-control"] == "no-store, no-cache"
        assert response.headers["referrer-policy"] == "no-referrer"

    def test_malformed_capabilities_are_uniform_404_for_get_and_post(self, native_settlement, now) -> None:
        client, _, _ = self._client(native_settlement, now)
        payer = str(Pubkey.new_unique())

        for capability in ("é" * 43, "A" * 44, f"{'A' * 42}!"):
            responses = (
                client.get(f"/commerce/solana/transaction-requests/{capability}"),
                client.post(
                    f"/commerce/solana/transaction-requests/{capability}",
                    json={"account": payer},
                ),
            )
            for response in responses:
                assert response.status_code == 404
                assert response.json() == {
                    "code": "capability_invalid",
                    "message": "Transaction request is unavailable.",
                }
                assert response.headers["cache-control"] == "no-store, no-cache"

    def test_capability_limit_and_rpc_errors_are_safe_and_private(self, native_settlement, now) -> None:
        cases = (
            (SolanaErrorCode.ISSUANCE_LIMIT_REACHED, 409, None),
            (SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE, 503, "5"),
        )
        for code, expected_status, retry_after in cases:
            service_error = SolanaCommerceError(code, "sensitive internal detail")
            client, _, _ = self._client(
                native_settlement,
                now,
                service_factory=lambda intent: FailingCapabilityService(intent, service_error),
            )
            response = client.post(
                f"/commerce/solana/transaction-requests/{VALID_CAPABILITY}",
                json={"account": str(Pubkey.new_unique())},
            )

            assert response.status_code == expected_status
            assert "sensitive" not in response.text
            assert response.headers["cache-control"] == "no-store, no-cache"
            assert response.headers.get("retry-after") == retry_after

    def test_capability_validation_error_is_no_store(self, native_settlement, now) -> None:
        client, _, _ = self._client(native_settlement, now)

        response = client.post(
            f"/commerce/solana/transaction-requests/{VALID_CAPABILITY}",
            json={"account": "not-a-public-key"},
        )

        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store, no-cache"

    def test_transaction_request_post_ignores_future_protocol_fields(self, native_settlement, now) -> None:
        client, _, _ = self._client(native_settlement, now)

        response = client.post(
            f"/commerce/solana/transaction-requests/{VALID_CAPABILITY}",
            json={
                "account": str(Pubkey.new_unique()),
                "future_wallet_extension": {"version": 2},
            },
        )

        assert response.status_code == 200
        assert response.json()["transaction"] == "dHJhbnNhY3Rpb24="
        assert response.headers["cache-control"] == "no-store, no-cache"

    def test_authenticated_action_reissue_checks_access_and_csrf(self, native_settlement, now) -> None:
        client, access, csrf_calls = self._client(native_settlement, now)
        payment_public_id = uuid4()

        response = client.post(f"/commerce/solana/payment-intents/{payment_public_id}/actions")

        assert response.status_code == 200, response.json()
        assert response.json()["kind"] == "solana_transaction_request"
        assert access.calls == [("user:7", payment_public_id, "payments:write")]
        assert csrf_calls == [True]
        assert response.headers["cache-control"] == "no-store, no-cache"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_authenticated_intent_with_action_is_no_store(self, native_settlement, now) -> None:
        client, _, _ = self._client(native_settlement, now)
        payment_public_id = uuid4()

        response = client.get(f"/commerce/solana/payment-intents/{payment_public_id}")

        assert response.status_code == 200
        assert response.json()["action"] is not None
        assert response.headers["cache-control"] == "no-store, no-cache"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_create_intent_with_capability_action_is_no_store(self, native_settlement, now) -> None:
        client, _, _ = self._client(native_settlement, now)

        response = client.post(
            "/commerce/solana/payment-intents",
            json={
                "order_public_id": str(uuid4()),
                "payment_option_id": "sol_native",
                "idempotency_key": "checkout-create-1",
            },
        )

        assert response.status_code == 201
        assert response.json()["action"] is not None
        assert response.headers["cache-control"] == "no-store, no-cache"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"

    def _client(
        self,
        settlement,
        now,
        *,
        service_factory=None,
    ) -> tuple[TestClient, FakeAccessPolicy, list[bool]]:
        action = SolanaCheckoutAction(
            kind=SolanaActionKind.TRANSACTION_REQUEST,
            uri="solana:https://pay.example.com/commerce/solana/transaction-requests/secret",
            expires_at=now + timedelta(minutes=5),
        )
        intent = SolanaIntentDTO(
            public_id=uuid4(),
            payment_public_id=uuid4(),
            order_public_id=uuid4(),
            state=SolanaIntentState.WAITING,
            settlement=settlement,
            action=action,
            revision=1,
            expires_at=now + timedelta(minutes=10),
            created_at=now,
            updated_at=now,
        )
        access = FakeAccessPolicy()
        csrf_calls: list[bool] = []

        def current_actor() -> SolanaActor:
            return SolanaActor(actor_key="user:7", user_id=7)

        def csrf() -> None:
            csrf_calls.append(True)

        app = FastAPI()
        service = FakeApiService(intent) if service_factory is None else service_factory(intent)
        app.include_router(
            SolanaFastApiRouterFactory.create(
                SolanaFastApiConfig(
                    service=service,
                    access=access,
                    current_actor_dependency=current_actor,
                    csrf_dependency=csrf,
                ),
            ),
        )
        return TestClient(app), access, csrf_calls
