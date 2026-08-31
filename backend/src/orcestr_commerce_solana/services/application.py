from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from commercexl import CommerceUserActorDTO, PaymentState
from sqlalchemy.ext.asyncio import AsyncSession

from orcestr_commerce_solana.integrations.fastapi import SolanaActor
from orcestr_commerce_solana.clock import Clock
from orcestr_commerce_solana.config import SolanaCommerceConfig
from orcestr_commerce_solana.rpc.protocol import SolanaRpc
from orcestr_commerce_solana.repositories import SolanaIntentRepository
from orcestr_commerce_solana.schemas.intents import (
    CancelIntentRequest,
    CandidateSignatureRequest,
    SolanaCheckoutAction,
    SolanaIntentCreateRequest,
    SolanaIntentDTO,
    SolanaPaymentOptionDTO,
    SolanaPaymentOptionsResponse,
)
from orcestr_commerce_solana.schemas.transactions import (
    SolanaPayGetResponse,
    SolanaPayPostRequest,
    SolanaPayPostResponse,
)
from orcestr_commerce_solana.services.candidate import SolanaCandidateProcessor
from orcestr_commerce_solana.services.intents.transaction_request import SolanaTransactionRequestService
from orcestr_commerce_solana.services.intents.builder import SolanaTransactionBuilder
from orcestr_commerce_solana.services.settlement import SolanaSettlementService
from orcestr_commerce_solana.services.serialization import SolanaIntentSerializer


class AsyncSessionFactory(Protocol):
    """Creates a host-owned control DB session context."""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Returns a new async session context."""
        ...


class SolanaApplicationService:
    """Provides reusable checkout orchestration; hosts supply only infrastructure ports."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        payment_runtime: object,
        intents: SolanaIntentRepository,
        candidates: SolanaCandidateProcessor,
        transaction_requests: SolanaTransactionRequestService,
    ) -> None:
        self.session_factory = session_factory
        self.payment_runtime = payment_runtime
        self.intents = intents
        self.candidates = candidates
        self.transaction_requests = transaction_requests

    @classmethod
    def build_default(
        cls,
        *,
        config: SolanaCommerceConfig,
        rpc: SolanaRpc,
        clock: Clock,
        session_factory: AsyncSessionFactory,
        payment_runtime: object,
        icon: str | None = None,
    ) -> SolanaApplicationService:
        """Wires default persistence, candidate verification, and transaction requests."""
        intents = SolanaIntentRepository(config, clock)
        settlement = SolanaSettlementService(payment_runtime, clock)
        candidates = SolanaCandidateProcessor(
            rpc,
            clock,
            settlement,
            max_issuances_per_intent=config.max_issuances_per_intent,
        )
        transaction_requests = SolanaTransactionRequestService(
            config,
            clock,
            intents,
            SolanaTransactionBuilder(rpc),
            icon=icon,
        )
        return cls(
            session_factory,
            payment_runtime,
            intents,
            candidates,
            transaction_requests,
        )

    async def list_payment_options(
        self,
        order_public_id: UUID,
        actor: SolanaActor,
    ) -> SolanaPaymentOptionsResponse:
        """Delegates ownership and provider selection to canonical CommerceXL runtime."""
        async with self.session_factory() as session:
            options = await self.payment_runtime.list_payment_options(
                session,
                order_public_id,
                self._commerce_actor(actor),
            )
            solana_options: list[SolanaPaymentOptionDTO] = []
            for option in options.options:
                if option.payment_system != "solana" or option.provider_kind != "solana":
                    continue
                details = option.details
                solana_options.append(
                    SolanaPaymentOptionDTO(
                        id=option.id,
                        label=option.label,
                        symbol=str(details["symbol"]),
                        decimals=int(details["decimals"]),
                        mint=str(details["mint"]) if details.get("mint") is not None else None,
                        minimum_raw_amount=str(details["minimum_raw_amount"]),
                        maximum_raw_amount=str(details["maximum_raw_amount"]),
                        required_commitment=str(details["required_commitment"]),
                    ),
                )
            return SolanaPaymentOptionsResponse(options=tuple(solana_options))

    async def create_intent(self, request: SolanaIntentCreateRequest, actor: SolanaActor) -> SolanaIntentDTO:
        """Creates the canonical attempt and child intent in one control DB transaction."""
        async with self.session_factory() as session, session.begin():
            payment = await self.payment_runtime.create_attempt(
                session,
                request.order_public_id,
                request.payment_option_id,
                self._commerce_actor(actor),
                request.idempotency_key,
            )
            intent = await self.intents.get_by_payment_public_id(session, payment.id)
            if intent is None:
                raise RuntimeError("CommerceXL Solana provider did not create an intent.")
            return SolanaIntentSerializer.serialize(intent, payment)

    async def get_intent(self, payment_public_id: UUID, actor: SolanaActor) -> SolanaIntentDTO:
        """Reads authoritative canonical and provider state after core ownership checks."""
        async with self.session_factory() as session:
            payment = await self.payment_runtime.get_payment_status(
                session,
                payment_public_id,
                self._commerce_actor(actor),
            )
            intent = await self.intents.get_by_payment_public_id(session, payment.id)
            if intent is None:
                raise self.payment_runtime.get_not_found("Solana payment intent not found.")
            return SolanaIntentSerializer.serialize(intent, payment)

    async def issue_action(self, payment_public_id: UUID, actor: SolanaActor) -> SolanaCheckoutAction:
        """Issues a fresh short-lived capability for an existing active intent."""
        async with self.session_factory() as session, session.begin():
            action = await self.payment_runtime.issue_checkout_action(
                session,
                payment_public_id,
                self._commerce_actor(actor),
            )
            if action.uri is None or action.expires_at is None:
                raise TypeError("Solana checkout action requires URI and expiry.")
            return SolanaCheckoutAction(kind=action.kind, uri=action.uri, expires_at=action.expires_at)

    async def submit_candidate(
        self,
        payment_public_id: UUID,
        request: CandidateSignatureRequest,
        actor: SolanaActor,
    ) -> SolanaIntentDTO:
        """Persists a hint, immediately verifies issued messages, and returns REST state."""
        async with self.session_factory() as session, session.begin():
            payment = await self.payment_runtime.get_payment_status(
                session,
                payment_public_id,
                self._commerce_actor(actor),
            )
            intent = await self.intents.record_candidate(session, payment_public_id, request.signature)
            await self.candidates.process(session, intent, request.signature, actor_key=actor.actor_key)
            payment = await self.payment_runtime.get_payment_status(
                session,
                payment_public_id,
                self._commerce_actor(actor),
            )
            return SolanaIntentSerializer.serialize(intent, payment)

    async def cancel_intent(
        self,
        payment_public_id: UUID,
        request: CancelIntentRequest,
        actor: SolanaActor,
    ) -> SolanaIntentDTO:
        """Cancels through CommerceXL dynamic provider contract and preserves audit reason."""
        async with self.session_factory() as session, session.begin():
            payment = await self.payment_runtime.get_payment_status(
                session,
                payment_public_id,
                self._commerce_actor(actor),
            )
            payment_state = PaymentState(payment.state)
            if payment_state == PaymentState.CANCELLED:
                intent = await self.intents.record_cancel_request(
                    session,
                    payment_public_id,
                    actor_key=actor.actor_key,
                    idempotency_key=request.idempotency_key,
                    reason=request.reason,
                )
                return SolanaIntentSerializer.serialize(intent, payment)
            if payment_state not in {
                PaymentState.CREATED,
                PaymentState.REQUIRES_ACTION,
                PaymentState.PROCESSING,
                PaymentState.REVIEW,
            }:
                raise self.payment_runtime.get_conflict(
                    "The selected payment attempt can no longer be cancelled.",
                )
            intent = await self.intents.record_cancel_request(
                session,
                payment_public_id,
                actor_key=actor.actor_key,
                idempotency_key=request.idempotency_key,
                reason=request.reason,
            )
            await self.payment_runtime.cancel_for_order(
                session,
                payment.order_id,
                self._commerce_actor(actor),
            )
            payment = await self.payment_runtime.get_payment_status(
                session,
                payment_public_id,
                self._commerce_actor(actor),
            )
            intent = await self.intents.get_by_payment_public_id(session, payment.id)
            if intent is None:
                raise self.payment_runtime.get_not_found("Solana payment intent not found.")
            return SolanaIntentSerializer.serialize(intent, payment)

    async def get_transaction_request(self, capability: str) -> SolanaPayGetResponse:
        """Resolves public merchant metadata using a control DB capability digest."""
        async with self.session_factory() as session, session.begin():
            return await self.transaction_requests.get(session, capability)

    async def create_transaction_request(
        self,
        capability: str,
        request: SolanaPayPostRequest,
    ) -> SolanaPayPostResponse:
        """Persists issuance before returning the unsigned transaction."""
        async with self.session_factory() as session, session.begin():
            return await self.transaction_requests.post(session, capability, request)

    @staticmethod
    def _commerce_actor(actor: SolanaActor) -> CommerceUserActorDTO:
        """Maps structural host auth identity into CommerceXL without auth imports."""
        if actor.user_id is None:
            raise ValueError("Authenticated Solana checkout requires a numeric CommerceXL user id.")
        return CommerceUserActorDTO(id=actor.user_id, permissions=actor.scopes)
