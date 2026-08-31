from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from orcestr_commerce_solana.errors import SolanaCommerceError, SolanaErrorCode
from orcestr_commerce_solana.schemas.errors import SolanaApiErrorDTO
from orcestr_commerce_solana.schemas.intents import (
    CancelIntentRequest,
    CandidateSignatureRequest,
    SolanaIntentCreateRequest,
    SolanaIntentDTO,
    SolanaCheckoutAction,
    SolanaPaymentOptionsResponse,
)
from orcestr_commerce_solana.schemas.transactions import (
    SolanaPayGetResponse,
    SolanaPayPostRequest,
    SolanaPayPostResponse,
)
from orcestr_commerce_solana.services.intents.capability import SolanaCapabilityService


class SolanaApiErrorMapper:
    """Maps package domain failures without exposing capability or RPC details."""

    retry_after_seconds = "5"

    @classmethod
    def response(cls, request: Request, error: SolanaCommerceError) -> JSONResponse:
        """Builds a safe deterministic response for one package-owned failure."""
        is_capability = "/transaction-requests/" in request.url.path
        code = error.code
        headers = cls.private_headers()
        if code in {
            SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE,
            SolanaErrorCode.RPC_INVALID_RESPONSE,
            SolanaErrorCode.WRONG_CLUSTER,
        }:
            headers["Retry-After"] = cls.retry_after_seconds
            return cls._response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE,
                "Solana service is temporarily unavailable.",
                headers,
            )
        if code == SolanaErrorCode.ISSUANCE_LIMIT_REACHED:
            return cls._response(
                status.HTTP_409_CONFLICT,
                code,
                "This payment intent cannot issue more transactions.",
                headers,
            )
        if is_capability:
            return cls._response(
                status.HTTP_404_NOT_FOUND,
                SolanaErrorCode.CAPABILITY_INVALID,
                "Transaction request is unavailable.",
                headers,
            )
        status_code = {
            SolanaErrorCode.ASSET_NOT_FOUND: status.HTTP_404_NOT_FOUND,
            SolanaErrorCode.IDEMPOTENCY_CONFLICT: status.HTTP_409_CONFLICT,
            SolanaErrorCode.INTENT_EXPIRED: status.HTTP_409_CONFLICT,
            SolanaErrorCode.CAPABILITY_EXPIRED: status.HTTP_409_CONFLICT,
        }.get(code, status.HTTP_400_BAD_REQUEST)
        message = {
            SolanaErrorCode.ASSET_NOT_FOUND: "Solana payment resource was not found.",
            SolanaErrorCode.IDEMPOTENCY_CONFLICT: "The idempotency key conflicts with an existing request.",
            SolanaErrorCode.INTENT_EXPIRED: "The Solana payment intent is no longer actionable.",
            SolanaErrorCode.CAPABILITY_EXPIRED: "The transaction request is no longer available.",
        }.get(code, "The Solana payment request could not be completed.")
        return cls._response(status_code, code, message, headers)

    @staticmethod
    def private_headers() -> dict[str, str]:
        """Returns headers that keep bearer-capability responses out of caches and referrers."""
        return {
            "Cache-Control": "no-store, no-cache",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        }

    @staticmethod
    def _response(
        status_code: int,
        code: SolanaErrorCode,
        message: str,
        headers: dict[str, str],
    ) -> JSONResponse:
        payload = SolanaApiErrorDTO(code=code, message=message)
        return JSONResponse(
            status_code=status_code,
            content=payload.model_dump(mode="json"),
            headers=headers,
        )


class SolanaApiRoute(APIRoute):
    """Adds package error handling without requiring a host-global exception handler."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            try:
                return await original(request)
            except SolanaCommerceError as error:
                return SolanaApiErrorMapper.response(request, error)
            except RequestValidationError as error:
                response = await request_validation_exception_handler(request, error)
                if "/transaction-requests/" in request.url.path:
                    response.headers.update(SolanaApiErrorMapper.private_headers())
                return response

        return route_handler


class SolanaActor(BaseModel):
    """Carries structural auth identity supplied by Orcestr Auth or another host."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_key: str = Field(min_length=1, max_length=100)
    user_id: int | None = None
    tenant_id: int | None = None
    scopes: frozenset[str] = frozenset()


class SolanaApiService(Protocol):
    """Defines typed application operations called by thin FastAPI routes."""

    async def list_payment_options(self, order_public_id: UUID, actor: SolanaActor) -> SolanaPaymentOptionsResponse:
        """Returns server-approved Solana options for one accessible order."""
        ...

    async def create_intent(self, request: SolanaIntentCreateRequest, actor: SolanaActor) -> SolanaIntentDTO:
        """Creates a CommerceXL attempt and immutable Solana intent idempotently."""
        ...

    async def get_intent(self, payment_public_id: UUID, actor: SolanaActor) -> SolanaIntentDTO:
        """Returns authoritative REST state for one accessible payment."""
        ...

    async def issue_action(self, payment_public_id: UUID, actor: SolanaActor) -> SolanaCheckoutAction:
        """Issues a fresh capability for reopening an active checkout."""
        ...

    async def submit_candidate(
        self,
        payment_public_id: UUID,
        request: CandidateSignatureRequest,
        actor: SolanaActor,
    ) -> SolanaIntentDTO:
        """Stores an untrusted candidate signature and triggers a bounded check."""
        ...

    async def cancel_intent(
        self,
        payment_public_id: UUID,
        request: CancelIntentRequest,
        actor: SolanaActor,
    ) -> SolanaIntentDTO:
        """Cancels an unpaid intent while retaining late-payment reconciliation."""
        ...

    async def get_transaction_request(self, capability: str) -> SolanaPayGetResponse:
        """Returns public merchant metadata for one active capability."""
        ...

    async def create_transaction_request(
        self,
        capability: str,
        request: SolanaPayPostRequest,
    ) -> SolanaPayPostResponse:
        """Builds and persists one unsigned transaction issuance."""
        ...


class SolanaAccessPolicy(Protocol):
    """Keeps ownership and permissions in the host instead of the addon domain."""

    async def check_order(self, actor: SolanaActor, order_public_id: UUID, action: str) -> None:
        """Raises a host error when order ownership or permission is missing."""
        ...

    async def check_payment(self, actor: SolanaActor, payment_public_id: UUID, action: str) -> None:
        """Raises a host error when payment ownership or permission is missing."""
        ...


ActorDependency = Callable[..., SolanaActor]
CsrfDependency = Callable[..., None]


@dataclass(frozen=True)
class SolanaFastApiConfig:
    """Injects auth, CSRF, ownership, and domain operations into a router factory."""

    service: SolanaApiService
    access: SolanaAccessPolicy
    current_actor_dependency: ActorDependency
    csrf_dependency: CsrfDependency
    prefix: str = "/commerce/solana"


class SolanaFastApiRouterFactory:
    """Builds typed routes without importing Orcestr Auth or application sessions."""

    @staticmethod
    def create(config: SolanaFastApiConfig) -> APIRouter:
        """Creates authenticated and public capability route groups."""
        router = APIRouter(prefix=config.prefix, tags=["commerce-solana"], route_class=SolanaApiRoute)

        @router.get(
            "/orders/{order_public_id}/payment-options",
            response_model=SolanaPaymentOptionsResponse,
        )
        async def list_payment_options(
            order_public_id: UUID,
            actor: SolanaActor = Depends(config.current_actor_dependency),
        ) -> SolanaPaymentOptionsResponse:
            await config.access.check_order(actor, order_public_id, "payments:read")
            return await config.service.list_payment_options(order_public_id, actor)

        @router.post(
            "/payment-intents",
            response_model=SolanaIntentDTO,
            status_code=status.HTTP_201_CREATED,
        )
        async def create_intent(
            request: SolanaIntentCreateRequest,
            response: Response,
            actor: SolanaActor = Depends(config.current_actor_dependency),
            csrf: None = Depends(config.csrf_dependency),
        ) -> SolanaIntentDTO:
            _ = csrf
            SolanaFastApiRouterFactory._set_private_headers(response)
            await config.access.check_order(actor, request.order_public_id, "payments:write")
            return await config.service.create_intent(request, actor)

        @router.get("/payment-intents/{payment_public_id}", response_model=SolanaIntentDTO)
        async def get_intent(
            payment_public_id: UUID,
            response: Response,
            actor: SolanaActor = Depends(config.current_actor_dependency),
        ) -> SolanaIntentDTO:
            SolanaFastApiRouterFactory._set_private_headers(response)
            await config.access.check_payment(actor, payment_public_id, "payments:read")
            return await config.service.get_intent(payment_public_id, actor)

        @router.post(
            "/payment-intents/{payment_public_id}/actions",
            response_model=SolanaCheckoutAction,
        )
        async def issue_action(
            payment_public_id: UUID,
            response: Response,
            actor: SolanaActor = Depends(config.current_actor_dependency),
            csrf: None = Depends(config.csrf_dependency),
        ) -> SolanaCheckoutAction:
            _ = csrf
            SolanaFastApiRouterFactory._set_private_headers(response)
            await config.access.check_payment(actor, payment_public_id, "payments:write")
            return await config.service.issue_action(payment_public_id, actor)

        @router.post(
            "/payment-intents/{payment_public_id}/candidate-signatures",
            response_model=SolanaIntentDTO,
        )
        async def submit_candidate(
            payment_public_id: UUID,
            request: CandidateSignatureRequest,
            response: Response,
            actor: SolanaActor = Depends(config.current_actor_dependency),
            csrf: None = Depends(config.csrf_dependency),
        ) -> SolanaIntentDTO:
            _ = csrf
            SolanaFastApiRouterFactory._set_private_headers(response)
            await config.access.check_payment(actor, payment_public_id, "payments:write")
            return await config.service.submit_candidate(payment_public_id, request, actor)

        @router.post("/payment-intents/{payment_public_id}/cancel", response_model=SolanaIntentDTO)
        async def cancel_intent(
            payment_public_id: UUID,
            request: CancelIntentRequest,
            response: Response,
            actor: SolanaActor = Depends(config.current_actor_dependency),
            csrf: None = Depends(config.csrf_dependency),
        ) -> SolanaIntentDTO:
            _ = csrf
            SolanaFastApiRouterFactory._set_private_headers(response)
            await config.access.check_payment(actor, payment_public_id, "payments:write")
            return await config.service.cancel_intent(payment_public_id, request, actor)

        @router.get("/transaction-requests/{capability}", response_model=SolanaPayGetResponse)
        async def get_transaction_request(capability: str, response: Response) -> SolanaPayGetResponse:
            SolanaFastApiRouterFactory._set_private_headers(response)
            SolanaCapabilityService.digest(capability)
            return await config.service.get_transaction_request(capability)

        @router.post("/transaction-requests/{capability}", response_model=SolanaPayPostResponse)
        async def create_transaction_request(
            capability: str,
            request: SolanaPayPostRequest,
            response: Response,
        ) -> SolanaPayPostResponse:
            SolanaFastApiRouterFactory._set_private_headers(response)
            SolanaCapabilityService.digest(capability)
            return await config.service.create_transaction_request(capability, request)

        return router

    @staticmethod
    def _set_private_headers(response: Response) -> None:
        """Prevents capability responses from entering caches or referrer headers."""
        response.headers.update(SolanaApiErrorMapper.private_headers())
