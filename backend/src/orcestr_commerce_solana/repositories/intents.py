from __future__ import annotations

import secrets
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from solders.pubkey import Pubkey
from solders.signature import Signature
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from commercexl import PaymentORM

from orcestr_commerce_solana.clock import Clock, database_utc_datetime, utc_datetime
from orcestr_commerce_solana.config import SolanaCommerceConfig
from orcestr_commerce_solana.errors import SolanaCommerceError, SolanaErrorCode
from orcestr_commerce_solana.models import (
    SolanaIntentCapabilityORM,
    SolanaPaymentIntentORM,
    SolanaTransactionIssuanceORM,
)
from orcestr_commerce_solana.schemas.assets import SettlementSnapshot
from orcestr_commerce_solana.schemas.intents import SolanaIntentState
from orcestr_commerce_solana.services.intents.capability import SolanaCapabilityService


class IntentCreation(BaseModel):
    """Carries provider facts ready for one atomic intent insert."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    payment_id: int
    order_public_id: UUID
    settlement: SettlementSnapshot
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime) -> datetime:
        return utc_datetime(value, "Intent expiry")


class IntentActionIssue(BaseModel):
    """Returns the ephemeral URI and persistent intent identity separately."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_public_id: UUID
    uri: str
    expires_at: datetime


class CapabilityIntent(BaseModel):
    """Returns an active intent after a constant-time capability lookup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_id: int
    capability_id: int
    intent_public_id: UUID
    payment_id: int
    settlement: SettlementSnapshot
    expires_at: datetime
    capability_expires_at: datetime


class SolanaIntentRepository:
    """Persists intent snapshots and capability digests in the host transaction."""

    def __init__(
        self,
        config: SolanaCommerceConfig,
        clock: Clock,
        capabilities: SolanaCapabilityService | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.capabilities = capabilities or SolanaCapabilityService()

    async def create(self, session: AsyncSession, creation: IntentCreation) -> IntentActionIssue:
        """Creates one child intent or reissues an action for an idempotent retry."""
        existing = await self.get_by_payment(session, creation.payment_id)
        if existing is not None:
            return await self.issue_action(session, existing)
        now = self.clock.now()
        settlement = creation.settlement
        intent = SolanaPaymentIntentORM(
            public_id=uuid4(),
            payment_id=creation.payment_id,
            order_public_id=creation.order_public_id,
            cluster=settlement.cluster,
            genesis_hash=settlement.genesis_hash,
            asset_option_id=settlement.asset_option_id,
            asset_kind=settlement.kind.value,
            mint=settlement.mint,
            token_program=settlement.token_program,
            decimals=settlement.decimals,
            recipient_wallet=settlement.recipient_wallet,
            recipient_token_account=settlement.recipient_token_account,
            expected_raw_amount=Decimal(settlement.expected_raw_amount),
            display_amount=settlement.display_amount,
            reference=settlement.reference,
            quote_snapshot=settlement.quote.model_dump(mode="json"),
            settlement_snapshot=settlement.model_dump(mode="json"),
            required_commitment=settlement.required_commitment.value,
            memo=settlement.memo,
            state=SolanaIntentState.WAITING.value,
            expires_at=creation.expires_at.astimezone(UTC),
            reconcile_until=(creation.expires_at + self.config.terminal_reconciliation_grace).astimezone(UTC),
            next_check_at=now,
            revision=0,
            created_at=now,
            updated_at=now,
        )
        session.add(intent)
        await session.flush()
        return await self.issue_action(session, intent)

    async def get_by_payment(
        self,
        session: AsyncSession,
        payment_id: int,
        *,
        for_update: bool = False,
    ) -> SolanaPaymentIntentORM | None:
        """Returns the provider child row for one canonical CommerceXL attempt."""
        query = select(SolanaPaymentIntentORM).where(SolanaPaymentIntentORM.payment_id == payment_id)
        if for_update:
            query = query.with_for_update()
        result = await session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_payment_public_id(
        self,
        session: AsyncSession,
        payment_public_id: UUID,
        *,
        for_update: bool = False,
    ) -> SolanaPaymentIntentORM | None:
        """Finds a provider intent through the public canonical payment identity."""
        query = (
            select(SolanaPaymentIntentORM)
            .join(PaymentORM, PaymentORM.id == SolanaPaymentIntentORM.payment_id)
            .where(PaymentORM.public_id == payment_public_id)
        )
        if for_update:
            query = query.with_for_update()
        return (await session.execute(query)).scalar_one_or_none()

    async def get_by_public_id(
        self,
        session: AsyncSession,
        intent_public_id: UUID,
        *,
        for_update: bool = False,
    ) -> SolanaPaymentIntentORM | None:
        """Finds one provider intent by its own public identity."""
        query = select(SolanaPaymentIntentORM).where(SolanaPaymentIntentORM.public_id == intent_public_id)
        if for_update:
            query = query.with_for_update()
        return (await session.execute(query)).scalar_one_or_none()

    async def record_candidate(
        self,
        session: AsyncSession,
        payment_public_id: UUID,
        signature: str,
    ) -> SolanaPaymentIntentORM:
        """Stores an untrusted acceleration hint without changing financial state."""
        intent = await self.get_by_payment_public_id(session, payment_public_id, for_update=True)
        if intent is None:
            raise SolanaCommerceError(SolanaErrorCode.ASSET_NOT_FOUND, "Solana payment intent was not found.")
        try:
            parsed_signature = Signature.from_string(signature)
        except ValueError as exc:
            raise SolanaCommerceError(SolanaErrorCode.SIGNATURE_MISMATCH, "Candidate signature is invalid.") from exc
        if str(parsed_signature) != signature:
            raise SolanaCommerceError(SolanaErrorCode.SIGNATURE_MISMATCH, "Candidate signature is not canonical.")
        if (
            intent.state != SolanaIntentState.WAITING.value
            or database_utc_datetime(intent.expires_at) <= self.clock.now()
        ):
            raise SolanaCommerceError(SolanaErrorCode.INTENT_EXPIRED, "Solana payment intent no longer accepts candidates.")
        if intent.candidate_signature == signature:
            return intent
        intent.candidate_signature = signature
        intent.next_check_at = self.clock.now()
        intent.updated_at = self.clock.now()
        await session.flush()
        return intent

    async def record_cancel_request(
        self,
        session: AsyncSession,
        payment_public_id: UUID,
        *,
        actor_key: str,
        idempotency_key: str,
        reason: str,
    ) -> SolanaPaymentIntentORM:
        """Persists one idempotent cancellation command before core provider cancel."""
        intent = await self.get_by_payment_public_id(session, payment_public_id, for_update=True)
        if intent is None:
            raise SolanaCommerceError(SolanaErrorCode.ASSET_NOT_FOUND, "Solana payment intent was not found.")
        existing_command = (
            await session.execute(
                select(SolanaPaymentIntentORM)
                .where(
                    SolanaPaymentIntentORM.cancel_actor_key == actor_key,
                    SolanaPaymentIntentORM.cancel_idempotency_key == idempotency_key,
                )
                .with_for_update(),
            )
        ).scalar_one_or_none()
        if existing_command is not None and existing_command.id != intent.id:
            raise SolanaCommerceError(
                SolanaErrorCode.IDEMPOTENCY_CONFLICT,
                "Cancellation idempotency key was already used for another payment.",
            )
        if intent.cancel_idempotency_key is not None:
            if (
                intent.cancel_idempotency_key == idempotency_key
                and intent.cancel_actor_key == actor_key
                and intent.cancel_reason == reason
            ):
                return intent
            raise SolanaCommerceError(
                SolanaErrorCode.IDEMPOTENCY_CONFLICT,
                "Cancellation idempotency key was already used with different data.",
            )
        intent.cancel_idempotency_key = idempotency_key
        intent.cancel_actor_key = actor_key
        intent.cancel_reason = reason
        intent.updated_at = self.clock.now()
        await session.flush()
        return intent

    async def issue_action(
        self,
        session: AsyncSession,
        intent: SolanaPaymentIntentORM,
    ) -> IntentActionIssue:
        """Persists a new digest while returning the raw capability only to this caller."""
        locked_intent = (
            await session.execute(
                select(SolanaPaymentIntentORM)
                .where(SolanaPaymentIntentORM.id == intent.id)
                .with_for_update(),
            )
        ).scalar_one_or_none()
        if locked_intent is None:
            raise SolanaCommerceError(SolanaErrorCode.ASSET_NOT_FOUND, "Solana payment intent was not found.")
        intent = locked_intent
        now = self.clock.now()
        intent_expires_at = database_utc_datetime(intent.expires_at)
        if intent_expires_at <= now or intent.state != SolanaIntentState.WAITING.value:
            raise SolanaCommerceError(SolanaErrorCode.INTENT_EXPIRED, "Solana payment intent is not actionable.")
        await self._revoke_excess(session, intent.id, now)
        capability_expires_at = min(intent_expires_at, now + self.config.capability_ttl)
        issue = self.capabilities.issue(capability_expires_at)
        capability = SolanaIntentCapabilityORM(
            public_id=issue.public_id,
            intent_id=intent.id,
            secret_sha256=issue.secret_sha256,
            expires_at=issue.expires_at,
            created_at=now,
        )
        session.add(capability)
        await session.flush()
        return IntentActionIssue(
            intent_public_id=intent.public_id,
            uri=self.capabilities.transaction_request_uri(self.config.public_base_url, issue.secret),
            expires_at=issue.expires_at,
        )

    async def get_by_capability(self, session: AsyncSession, secret: str) -> CapabilityIntent:
        """Resolves one active digest without exposing whether invalid capabilities existed."""
        digest = self.capabilities.digest(secret)
        lookup = (
            await session.execute(
                select(SolanaIntentCapabilityORM.id, SolanaIntentCapabilityORM.intent_id)
                .where(SolanaIntentCapabilityORM.secret_sha256 == digest)
            )
        ).one_or_none()
        now = self.clock.now()
        if lookup is None:
            raise SolanaCommerceError(SolanaErrorCode.CAPABILITY_INVALID, "Transaction request is unavailable.")
        intent = (
            await session.execute(
                select(SolanaPaymentIntentORM)
                .where(SolanaPaymentIntentORM.id == lookup.intent_id)
                .with_for_update(),
            )
        ).scalar_one_or_none()
        capability = (
            await session.execute(
                select(SolanaIntentCapabilityORM)
                .where(
                    SolanaIntentCapabilityORM.id == lookup.id,
                    SolanaIntentCapabilityORM.intent_id == lookup.intent_id,
                    SolanaIntentCapabilityORM.secret_sha256 == digest,
                )
                .with_for_update(),
            )
        ).scalar_one_or_none()
        if (
            intent is None
            or capability is None
            or capability.revoked_at is not None
            or database_utc_datetime(capability.expires_at) <= now
            or database_utc_datetime(intent.expires_at) <= now
            or intent.state != SolanaIntentState.WAITING.value
        ):
            raise SolanaCommerceError(SolanaErrorCode.CAPABILITY_INVALID, "Transaction request is unavailable.")
        capability.last_used_at = now
        settlement = SettlementSnapshot.model_validate(intent.settlement_snapshot)
        return CapabilityIntent(
            intent_id=intent.id,
            capability_id=capability.id,
            intent_public_id=intent.public_id,
            payment_id=intent.payment_id,
            settlement=settlement,
            expires_at=database_utc_datetime(intent.expires_at),
            capability_expires_at=database_utc_datetime(capability.expires_at),
        )

    async def cancel(self, session: AsyncSession, payment_id: int, reason_code: str) -> None:
        """Cancels an unpaid intent and revokes all transaction-request capabilities."""
        intent = await self.get_by_payment(session, payment_id, for_update=True)
        if intent is None:
            raise SolanaCommerceError(SolanaErrorCode.ASSET_NOT_FOUND, "Solana payment intent was not found.")
        now = self.clock.now()
        if intent.state == SolanaIntentState.PAID.value:
            raise SolanaCommerceError(SolanaErrorCode.INVALID_CONFIGURATION, "Paid Solana intent cannot be cancelled.")
        intent.state = SolanaIntentState.CANCELLED.value
        intent.reason_code = intent.cancel_reason or reason_code
        intent.revision += 1
        intent.updated_at = now
        issuance_count = await self.count_issuances(session, intent.id)
        intent.next_check_at = (
            now
            if issuance_count > 0 and now < database_utc_datetime(intent.reconcile_until)
            else None
        )
        result = await session.execute(
            select(SolanaIntentCapabilityORM)
            .where(
                SolanaIntentCapabilityORM.intent_id == intent.id,
                SolanaIntentCapabilityORM.revoked_at.is_(None),
            )
            .with_for_update(),
        )
        for capability in result.scalars():
            capability.revoked_at = now
        await session.flush()

    @staticmethod
    async def count_issuances(session: AsyncSession, intent_id: int) -> int:
        """Counts persisted unsigned messages while the caller holds the intent lock."""
        count = await session.scalar(
            select(func.count(SolanaTransactionIssuanceORM.id)).where(
                SolanaTransactionIssuanceORM.intent_id == intent_id,
            ),
        )
        return int(count or 0)

    async def _revoke_excess(self, session: AsyncSession, intent_id: int, now: datetime) -> None:
        """Keeps the configured number of concurrently active capabilities bounded."""
        result = await session.execute(
            select(SolanaIntentCapabilityORM)
            .where(
                SolanaIntentCapabilityORM.intent_id == intent_id,
                SolanaIntentCapabilityORM.revoked_at.is_(None),
                SolanaIntentCapabilityORM.expires_at > now,
            )
            .order_by(SolanaIntentCapabilityORM.created_at.desc())
            .with_for_update(),
        )
        active = list(result.scalars())
        keep_existing = max(self.config.max_active_capabilities - 1, 0)
        for capability in active[keep_existing:]:
            capability.revoked_at = now

    @staticmethod
    def new_reference() -> str:
        """Creates an unpredictable reference public key that never needs to sign."""
        return str(Pubkey.from_bytes(secrets.token_bytes(32)))
