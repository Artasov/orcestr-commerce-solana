from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from urllib.parse import unquote

import pytest
from commercexl import (
    CommerceBase,
    OrderORM,
    OrderState,
    PaymentORM,
    PaymentState,
    get_default_commerce_module,
)
from solders.signature import Signature
from solders.pubkey import Pubkey
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from orcestr_commerce_solana.clock import FrozenClock, database_utc_datetime
from orcestr_commerce_solana.config import SolanaCommerceConfig
from orcestr_commerce_solana.errors import SolanaCommerceError, SolanaErrorCode
from orcestr_commerce_solana.models import (
    SolanaIntentCapabilityORM,
    SolanaPaymentEventORM,
    SolanaPaymentIntentORM,
    SolanaTransactionIssuanceORM,
    SolanaTransferORM,
)
from orcestr_commerce_solana.repositories import SolanaIntentRepository
from orcestr_commerce_solana.integrations.commercexl import CommerceVerificationMapper
from orcestr_commerce_solana.schemas.assets import SettlementSnapshot, SolanaCluster
from orcestr_commerce_solana.schemas.intents import SolanaIntentState
from orcestr_commerce_solana.schemas.transactions import TransactionVersion
from orcestr_commerce_solana.schemas.transactions import SolanaPayPostRequest
from orcestr_commerce_solana.schemas.assets import SolanaCommitment
from orcestr_commerce_solana.services.intents.builder import SolanaTransactionBuilder
from orcestr_commerce_solana.services.intents.transaction_request import SolanaTransactionRequestService
from orcestr_commerce_solana.services.candidate import SolanaCandidateProcessor
from orcestr_commerce_solana.services.settlement import SolanaSettlementService
from orcestr_commerce_solana.services.reconciliation_store import SqlAlchemyReconciliationStore
from orcestr_commerce_solana.services.verification import (
    VerificationDisposition,
    VerificationResult,
    VerifiedTransfer,
)
from conftest import FakeRpc
from orcestr_commerce_solana.rpc.types import RpcSignatureStatus


async def add_payment_and_intent(
    session,
    now,
    settlement: SettlementSnapshot,
    state: SolanaIntentState,
    *,
    order_id: UUID | None = None,
):
    """Adds the smallest canonical payment and provider row needed by repository joins."""
    payment_public_id = uuid4()
    payment = PaymentORM(
        public_id=payment_public_id,
        order_id=order_id or uuid4(),
        attempt_no=1,
        active_slot=None if state in {SolanaIntentState.CANCELLED, SolanaIntentState.FAILED} else 1,
        user_id=1,
        amount=Decimal("10"),
        currency="USD",
        payment_system="solana",
        kind="solana",
        payment_option_id=settlement.asset_option_id,
        state=(
            PaymentState.CANCELLED.value
            if state == SolanaIntentState.CANCELLED
            else PaymentState.REQUIRES_ACTION.value
        ),
        action_kind="solana_transaction_request",
        idempotency_key=str(uuid4()),
        idempotency_fingerprint="0" * 64,
        revision=0,
        created_at=now,
        updated_at=now,
    )
    session.add(payment)
    await session.flush()
    intent = SolanaPaymentIntentORM(
        public_id=uuid4(),
        payment_id=payment.id,
        order_public_id=payment.order_id,
        cluster=settlement.cluster.value,
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
        state=state.value,
        next_check_at=now,
        expires_at=now + timedelta(minutes=10),
        reconcile_until=now + timedelta(minutes=15),
        revision=0,
        created_at=now,
        updated_at=now,
    )
    session.add(intent)
    await session.flush()
    return payment_public_id, intent


class TestSolanaIntentRepository:
    """Covers terminal candidate guards and cancellation command idempotency."""

    @pytest.mark.asyncio
    async def test_terminal_intent_rejects_candidate(self, now, native_settlement) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repository = SolanaIntentRepository(self._config(), FrozenClock(now))
        async with factory() as session, session.begin():
            payment_public_id, _ = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.CANCELLED,
            )
            with pytest.raises(SolanaCommerceError) as error:
                await repository.record_candidate(session, payment_public_id, str(Signature.new_unique()))
            assert error.value.code == SolanaErrorCode.INTENT_EXPIRED
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_elapsed_waiting_intent_rejects_candidate_before_fast_path(self, now, native_settlement) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repository = SolanaIntentRepository(self._config(), FrozenClock(now))
        async with factory() as session, session.begin():
            payment_public_id, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.WAITING,
            )
            intent.expires_at = now - timedelta(seconds=1)
            with pytest.raises(SolanaCommerceError) as error:
                await repository.record_candidate(session, payment_public_id, str(Signature.new_unique()))

            assert error.value.code == SolanaErrorCode.INTENT_EXPIRED
            assert intent.candidate_signature is None
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_cancel_command_is_idempotent_and_payload_bound(self, now, native_settlement) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repository = SolanaIntentRepository(self._config(), FrozenClock(now))
        async with factory() as session, session.begin():
            payment_public_id, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.WAITING,
            )
            first = await repository.record_cancel_request(
                session,
                payment_public_id,
                actor_key="user:1",
                idempotency_key="cancel-command-1",
                reason="changed_mind",
            )
            repeated = await repository.record_cancel_request(
                session,
                payment_public_id,
                actor_key="user:1",
                idempotency_key="cancel-command-1",
                reason="changed_mind",
            )
            assert first is repeated is intent
            assert repeated.cancel_reason == "changed_mind"
            with pytest.raises(SolanaCommerceError) as error:
                await repository.record_cancel_request(
                    session,
                    payment_public_id,
                    actor_key="user:1",
                    idempotency_key="cancel-command-1",
                    reason="different_reason",
                )
            assert error.value.code == SolanaErrorCode.IDEMPOTENCY_CONFLICT
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_issuance_limit_is_checked_before_rpc_and_insert(self, now, native_settlement) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        config = self._config().model_copy(update={"max_issuances_per_intent": 2})
        repository = SolanaIntentRepository(config, FrozenClock(now))

        class CountingRpc(FakeRpc):
            def __init__(self) -> None:
                super().__init__()
                self.latest_calls = 0

            async def get_latest_blockhash(self, commitment):
                self.latest_calls += 1
                return await super().get_latest_blockhash(commitment)

        rpc = CountingRpc()
        service = SolanaTransactionRequestService(
            config,
            FrozenClock(now),
            repository,
            SolanaTransactionBuilder(rpc),
        )
        async with factory() as session, session.begin():
            _, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.WAITING,
            )
            action = await repository.issue_action(session, intent)
            capability = unquote(action.uri.rsplit("/", 1)[-1])
            payer = str(Pubkey.new_unique())
            for _ in range(2):
                await service.post(
                    session,
                    capability,
                    SolanaPayPostRequest(account=payer),
                )

            with pytest.raises(SolanaCommerceError) as error:
                await service.post(
                    session,
                    capability,
                    SolanaPayPostRequest(account=str(Pubkey.new_unique())),
                )

            issuance_count = await repository.count_issuances(session, intent.id)
            context_slots = tuple(
                (
                    await session.execute(
                        select(SolanaTransactionIssuanceORM.issued_context_slot).where(
                            SolanaTransactionIssuanceORM.intent_id == intent.id,
                        ),
                    )
                ).scalars()
            )
            message_hashes = tuple(
                (
                    await session.execute(
                        select(SolanaTransactionIssuanceORM.message_sha256).where(
                            SolanaTransactionIssuanceORM.intent_id == intent.id,
                        ),
                    )
                ).scalars()
            )
            assert error.value.code == SolanaErrorCode.ISSUANCE_LIMIT_REACHED
            assert issuance_count == 2
            assert rpc.latest_calls == 2
            assert context_slots == (800, 800)
            assert len(set(message_hashes)) == 2
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_confirmed_payment_revokes_actions_and_blocks_more_issuances(self, now, native_settlement) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        config = self._config()
        repository = SolanaIntentRepository(config, FrozenClock(now))
        rpc = FakeRpc()
        request_service = SolanaTransactionRequestService(
            config,
            FrozenClock(now),
            repository,
            SolanaTransactionBuilder(rpc),
        )
        runtime = FakePaymentRuntime()
        settlement_service = SolanaSettlementService(runtime, FrozenClock(now))
        async with factory() as session, session.begin():
            _, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.WAITING,
            )
            action = await repository.issue_action(session, intent)
            capability = unquote(action.uri.rsplit("/", 1)[-1])
            payer = str(Pubkey.new_unique())
            await request_service.post(session, capability, SolanaPayPostRequest(account=payer))
            issuance = await session.scalar(
                select(SolanaTransactionIssuanceORM).where(
                    SolanaTransactionIssuanceORM.intent_id == intent.id,
                ),
            )
            assert issuance is not None
            signature = str(Signature.new_unique())
            confirmed = VerificationResult(
                disposition=VerificationDisposition.CONFIRMED,
                reason_code=SolanaErrorCode.TRANSACTION_NOT_FINAL.value,
                retryable=True,
                commitment=SolanaCommitment.CONFIRMED,
                transfer=VerifiedTransfer(
                    cluster=native_settlement.cluster,
                    signature=signature,
                    issuance_public_id=issuance.public_id,
                    instruction_index=1,
                    source_account=payer,
                    destination_account=native_settlement.recipient_wallet,
                    gross_raw_amount=native_settlement.expected_raw_amount,
                    net_raw_amount=native_settlement.expected_raw_amount,
                    slot=900,
                    block_time=now,
                    commitment=SolanaCommitment.CONFIRMED,
                    transaction_version=TransactionVersion.V0,
                    detection_source="reference_scan",
                    evidence_sha256="9" * 64,
                ),
            )
            await settlement_service.apply(session, intent, confirmed)

            with pytest.raises(SolanaCommerceError) as action_error:
                await repository.issue_action(session, intent)
            with pytest.raises(SolanaCommerceError) as post_error:
                await request_service.post(
                    session,
                    capability,
                    SolanaPayPostRequest(account=str(Pubkey.new_unique())),
                )

            assert action_error.value.code == SolanaErrorCode.INTENT_EXPIRED
            assert post_error.value.code == SolanaErrorCode.CAPABILITY_INVALID
            assert await repository.count_issuances(session, intent.id) == 1
            capability_row = await session.scalar(
                select(SolanaIntentCapabilityORM).where(
                    SolanaIntentCapabilityORM.intent_id == intent.id,
                ),
            )
            assert capability_row is not None
            assert database_utc_datetime(capability_row.revoked_at) == now
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_untrusted_processed_candidate_keeps_waiting_actionable(self, now, native_settlement) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        config = self._config()
        repository = SolanaIntentRepository(config, FrozenClock(now))
        runtime = FakePaymentRuntime()
        rpc = FakeRpc()
        rpc.status = RpcSignatureStatus(slot=900, confirmation_status="processed")
        processor = SolanaCandidateProcessor(
            rpc,
            FrozenClock(now),
            SolanaSettlementService(runtime, FrozenClock(now)),
            max_issuances_per_intent=config.max_issuances_per_intent,
        )
        async with factory() as session, session.begin():
            _, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.WAITING,
            )
            action = await repository.issue_action(session, intent)
            capability_row = await session.scalar(
                select(SolanaIntentCapabilityORM).where(
                    SolanaIntentCapabilityORM.intent_id == intent.id,
                ),
            )
            assert capability_row is not None
            session.add(
                SolanaTransactionIssuanceORM(
                    public_id=uuid4(),
                    intent_id=intent.id,
                    capability_id=capability_row.id,
                    payer=str(Pubkey.new_unique()),
                    recent_blockhash=str(Pubkey.new_unique()),
                    last_valid_block_height=1000,
                    issued_context_slot=800,
                    message_sha256="a" * 64,
                    transaction_version=TransactionVersion.V0.value,
                    issued_at=now,
                    accepts_until=now + timedelta(minutes=5),
                ),
            )
            await session.flush()

            result = await processor.process(session, intent, str(Signature.new_unique()))
            reissued = await repository.issue_action(session, intent)

            assert result is not None
            assert result.disposition == VerificationDisposition.OBSERVED
            assert intent.state == SolanaIntentState.WAITING.value
            assert runtime.results == []
            assert reissued.uri != action.uri
        await engine.dispose()

    @staticmethod
    def _config() -> SolanaCommerceConfig:
        return SolanaCommerceConfig(
            public_base_url="https://pay.example.com",
            merchant_label="Merchant",
            enable_native_sol=True,
        )


class FakePaymentRuntime:
    """Captures CommerceXL verification effects without duplicating its state machine."""

    def __init__(self) -> None:
        self.results = []

    async def apply_verification(self, session, payment_id, result) -> None:
        _ = session
        _ = payment_id
        self.results.append(result)


class TestSolanaSettlementService:
    """Pins idempotent application from provisional observation through finalized payment."""

    @pytest.mark.asyncio
    async def test_repeated_confirmation_and_finalization_do_not_duplicate_effects(
        self,
        now,
        native_settlement,
    ) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        runtime = FakePaymentRuntime()
        settlement = SolanaSettlementService(runtime, FrozenClock(now))
        signature = str(Signature.new_unique())
        issuance_public_id = uuid4()
        transfer = VerifiedTransfer(
            cluster=native_settlement.cluster,
            signature=signature,
            issuance_public_id=issuance_public_id,
            instruction_index=0,
            source_account=str(Pubkey.new_unique()),
            destination_account=native_settlement.recipient_wallet,
            gross_raw_amount=native_settlement.expected_raw_amount,
            net_raw_amount=native_settlement.expected_raw_amount,
            slot=100,
            block_time=now,
            commitment=SolanaCommitment.CONFIRMED,
            transaction_version=TransactionVersion.V0,
            detection_source="candidate",
            evidence_sha256="1" * 64,
        )
        confirmed = VerificationResult(
            disposition=VerificationDisposition.CONFIRMED,
            reason_code="transaction_not_final",
            retryable=True,
            commitment=SolanaCommitment.CONFIRMED,
            transfer=transfer,
        )
        finalized = VerificationResult(
            disposition=VerificationDisposition.MATCH,
            commitment=SolanaCommitment.FINALIZED,
            transfer=transfer.model_copy(update={"commitment": SolanaCommitment.FINALIZED}),
        )
        async with factory() as session, session.begin():
            _, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.WAITING,
            )
            capability = SolanaIntentCapabilityORM(
                public_id=uuid4(),
                intent_id=intent.id,
                secret_sha256="2" * 64,
                expires_at=now + timedelta(minutes=5),
                created_at=now,
            )
            session.add(capability)
            await session.flush()
            session.add(
                SolanaTransactionIssuanceORM(
                    public_id=issuance_public_id,
                    intent_id=intent.id,
                    capability_id=capability.id,
                    payer=str(Pubkey.new_unique()),
                    recent_blockhash=str(Pubkey.new_unique()),
                    last_valid_block_height=1000,
                    issued_context_slot=800,
                    message_sha256="3" * 64,
                    transaction_version=TransactionVersion.V0.value,
                    issued_at=now,
                    accepts_until=now + timedelta(minutes=5),
                ),
            )
            await session.flush()
            observed = VerificationResult(
                disposition=VerificationDisposition.OBSERVED,
                reason_code="transaction_not_final",
                retryable=True,
            )
            await settlement.apply(session, intent, observed)
            await settlement.apply(session, intent, observed)
            await settlement.apply(session, intent, confirmed)
            await settlement.apply(session, intent, confirmed)
            await settlement.apply(session, intent, finalized)
            await settlement.apply(session, intent, finalized)
            event_count = await session.scalar(select(func.count()).select_from(SolanaPaymentEventORM))

            assert event_count == 3
            assert intent.revision == 3
            assert intent.state == SolanaIntentState.PAID.value
            assert [result.state for result in runtime.results] == [
                PaymentState.PROCESSING,
                PaymentState.CONFIRMED,
                PaymentState.PAID,
            ]
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sql_store_leases_due_rows_and_expires_atomically(self, now, native_settlement) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        runtime = FakePaymentRuntime()
        clock = FrozenClock(now)
        store = SqlAlchemyReconciliationStore(
            factory,
            SolanaSettlementService(runtime, clock),
            clock,
            max_issuances_per_intent=16,
            claim_lease=timedelta(seconds=30),
        )
        async with factory() as session, session.begin():
            _, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.WAITING,
            )
            intent_id = intent.public_id

        claimed = await store.list_pending(limit=10, now=now)
        duplicate_claim = await store.list_pending(limit=10, now=now)
        await store.expire(intent_id)

        assert [item.public_id for item in claimed] == [intent_id]
        assert duplicate_claim == ()
        assert runtime.results[-1].state == PaymentState.EXPIRED
        async with factory() as session:
            persisted = await session.scalar(
                select(SolanaPaymentIntentORM).where(SolanaPaymentIntentORM.public_id == intent_id),
            )
            assert persisted is not None
            assert persisted.state == SolanaIntentState.EXPIRED.value
            assert persisted.next_check_at is None
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_expiry_with_issuance_remains_scheduled_for_terminal_grace(self, now, native_settlement) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        runtime = FakePaymentRuntime()
        clock = FrozenClock(now)
        store = SqlAlchemyReconciliationStore(
            factory,
            SolanaSettlementService(runtime, clock),
            clock,
            max_issuances_per_intent=16,
            retry_delay=timedelta(seconds=5),
        )
        async with factory() as session, session.begin():
            _, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.WAITING,
            )
            capability = SolanaIntentCapabilityORM(
                public_id=uuid4(),
                intent_id=intent.id,
                secret_sha256="2" * 64,
                expires_at=now + timedelta(minutes=5),
                created_at=now,
            )
            session.add(capability)
            await session.flush()
            session.add(
                SolanaTransactionIssuanceORM(
                    public_id=uuid4(),
                    intent_id=intent.id,
                    capability_id=capability.id,
                    payer=str(Pubkey.new_unique()),
                    recent_blockhash=str(Pubkey.new_unique()),
                    last_valid_block_height=1000,
                    issued_context_slot=800,
                    message_sha256="3" * 64,
                    transaction_version=TransactionVersion.V0.value,
                    issued_at=now,
                    accepts_until=now + timedelta(minutes=5),
                ),
            )
            intent_public_id = intent.public_id

        await store.expire(intent_public_id)

        async with factory() as session:
            persisted = await session.scalar(
                select(SolanaPaymentIntentORM).where(
                    SolanaPaymentIntentORM.public_id == intent_public_id,
                ),
            )
            assert persisted is not None
            assert persisted.state == SolanaIntentState.EXPIRED.value
            assert database_utc_datetime(persisted.next_check_at) == now + timedelta(seconds=5)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_horizon_expiry_with_issuance_stays_recoverable_until_explicit_finish(
        self,
        now,
        native_settlement,
    ) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        clock = FrozenClock(now)
        store = SqlAlchemyReconciliationStore(
            factory,
            SolanaSettlementService(FakePaymentRuntime(), clock),
            clock,
            max_issuances_per_intent=16,
            retry_delay=timedelta(seconds=5),
        )
        async with factory() as session, session.begin():
            _, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.WAITING,
            )
            intent.expires_at = now - timedelta(minutes=2)
            intent.reconcile_until = now - timedelta(minutes=1)
            capability = SolanaIntentCapabilityORM(
                public_id=uuid4(),
                intent_id=intent.id,
                secret_sha256="9" * 64,
                expires_at=now - timedelta(minutes=2),
                created_at=now - timedelta(minutes=10),
            )
            session.add(capability)
            await session.flush()
            session.add(
                SolanaTransactionIssuanceORM(
                    public_id=uuid4(),
                    intent_id=intent.id,
                    capability_id=capability.id,
                    payer=str(Pubkey.new_unique()),
                    recent_blockhash=str(Pubkey.new_unique()),
                    last_valid_block_height=1000,
                    issued_context_slot=800,
                    message_sha256="a" * 64,
                    transaction_version=TransactionVersion.V0.value,
                    issued_at=now - timedelta(minutes=10),
                    accepts_until=now - timedelta(minutes=2),
                ),
            )
            intent_public_id = intent.public_id

        await store.expire(intent_public_id)

        async with factory() as session:
            persisted = await session.scalar(
                select(SolanaPaymentIntentORM).where(
                    SolanaPaymentIntentORM.public_id == intent_public_id,
                ),
            )
            assert persisted is not None
            assert persisted.state == SolanaIntentState.EXPIRED.value
            assert database_utc_datetime(persisted.next_check_at) == now + timedelta(seconds=5)

        await store.finish_terminal(intent_public_id)

        async with factory() as session:
            persisted = await session.scalar(
                select(SolanaPaymentIntentORM).where(
                    SolanaPaymentIntentORM.public_id == intent_public_id,
                ),
            )
            assert persisted is not None
            assert persisted.next_check_at is None
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_confirmed_intent_expires_through_real_commercexl_runtime(
        self,
        now,
        native_settlement,
    ) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        clock = FrozenClock(now)
        store = SqlAlchemyReconciliationStore(
            factory,
            SolanaSettlementService(get_default_commerce_module().create_payment_runtime(), clock),
            clock,
            max_issuances_per_intent=16,
        )
        order_id = uuid4()
        async with factory() as session, session.begin():
            session.add(
                OrderORM(
                    id=order_id,
                    user_id=1,
                    amount=Decimal("10"),
                    currency="USD",
                    idempotency_key=str(uuid4()),
                    idempotency_fingerprint="4" * 64,
                    state=OrderState.READY_FOR_PAYMENT.value,
                    kind="test_order",
                    created_at=now,
                    updated_at=now,
                ),
            )
            await session.flush()
            _, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.CONFIRMED,
                order_id=order_id,
            )
            payment = await session.get(PaymentORM, intent.payment_id)
            assert payment is not None
            payment.state = PaymentState.CONFIRMED.value
            intent_public_id = intent.public_id
            payment_id = payment.id

        await store.expire(intent_public_id)

        async with factory() as session:
            persisted_intent = await session.scalar(
                select(SolanaPaymentIntentORM).where(
                    SolanaPaymentIntentORM.public_id == intent_public_id,
                ),
            )
            persisted_payment = await session.get(PaymentORM, payment_id)
            assert persisted_intent is not None
            assert persisted_payment is not None
            assert persisted_intent.state == SolanaIntentState.EXPIRED.value
            assert persisted_payment.payment_state == PaymentState.EXPIRED
            assert persisted_payment.active_slot is None
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_terminal_late_transfer_updates_core_evidence_without_product_effect(
        self,
        now,
        native_settlement,
    ) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        runtime = FakePaymentRuntime()
        service = SolanaSettlementService(runtime, FrozenClock(now))
        async with factory() as session, session.begin():
            _, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.EXPIRED,
            )
            capability = SolanaIntentCapabilityORM(
                public_id=uuid4(),
                intent_id=intent.id,
                secret_sha256="4" * 64,
                expires_at=now,
                created_at=now - timedelta(minutes=10),
            )
            session.add(capability)
            await session.flush()
            issuance_public_id = uuid4()
            session.add(
                SolanaTransactionIssuanceORM(
                    public_id=issuance_public_id,
                    intent_id=intent.id,
                    capability_id=capability.id,
                    payer=str(Pubkey.new_unique()),
                    recent_blockhash=str(Pubkey.new_unique()),
                    last_valid_block_height=1000,
                    issued_context_slot=800,
                    message_sha256="5" * 64,
                    transaction_version=TransactionVersion.V0.value,
                    issued_at=now - timedelta(minutes=10),
                    accepts_until=now - timedelta(minutes=5),
                ),
            )
            result = VerificationResult(
                disposition=VerificationDisposition.REVIEW,
                reason_code=SolanaErrorCode.LATE_PAYMENT.value,
                commitment=SolanaCommitment.FINALIZED,
                transfer=VerifiedTransfer(
                    cluster=native_settlement.cluster,
                    signature=str(Signature.new_unique()),
                    issuance_public_id=issuance_public_id,
                    instruction_index=1,
                    source_account=str(Pubkey.new_unique()),
                    destination_account=native_settlement.recipient_wallet,
                    gross_raw_amount=native_settlement.expected_raw_amount,
                    net_raw_amount=native_settlement.expected_raw_amount,
                    slot=950,
                    block_time=now,
                    commitment=SolanaCommitment.FINALIZED,
                    transaction_version=TransactionVersion.V0,
                    detection_source="reference_scan",
                    evidence_sha256="6" * 64,
                ),
            )

            await service.record_terminal_evidence(session, intent, result)
            await service.record_terminal_evidence(session, intent, result)
            second_result = result.model_copy(
                update={
                    "transfer": result.transfer.model_copy(
                        update={
                            "signature": str(Signature.new_unique()),
                            "evidence_sha256": "8" * 64,
                        },
                    ),
                },
            )
            await service.record_terminal_evidence(session, intent, second_result)
            intent_public_id = intent.public_id
            recorded_signatures = {
                result.transfer.signature,
                second_result.transfer.signature,
            }
            events = tuple(
                (
                    await session.execute(
                        select(SolanaPaymentEventORM).where(
                            SolanaPaymentEventORM.intent_id == intent.id,
                        ),
                    )
                ).scalars()
            )

            assert intent.state == SolanaIntentState.EXPIRED.value
            assert len(events) == 2
            assert all(event.metadata_json["product_effect"] is False for event in events)
            assert intent.verified_signature == result.transfer.signature
            assert len(runtime.results) == 2
            assert all(item.state == PaymentState.EXPIRED for item in runtime.results)
            assert all(item.evidence_key.startswith("mainnet-beta:") for item in runtime.results)
        store = SqlAlchemyReconciliationStore(
            factory,
            service,
            FrozenClock(now),
            max_issuances_per_intent=16,
        )
        snapshots = await store.list_pending(limit=1, now=now)
        assert len(snapshots) == 1
        assert snapshots[0].public_id == intent_public_id
        assert set(snapshots[0].recorded_signatures) == recorded_signatures
        await engine.dispose()

    def test_commerce_evidence_identity_is_cluster_scoped(self, now, native_settlement) -> None:
        transfer = VerifiedTransfer(
            cluster=SolanaCluster.MAINNET_BETA,
            signature=str(Signature.new_unique()),
            issuance_public_id=uuid4(),
            instruction_index=1,
            source_account=str(Pubkey.new_unique()),
            destination_account=native_settlement.recipient_wallet,
            gross_raw_amount=native_settlement.expected_raw_amount,
            net_raw_amount=native_settlement.expected_raw_amount,
            slot=1,
            block_time=now,
            commitment=SolanaCommitment.FINALIZED,
            transaction_version=TransactionVersion.V0,
            detection_source="reference_scan",
            evidence_sha256="7" * 64,
        )

        mainnet_key = CommerceVerificationMapper.evidence_key(transfer)
        devnet_key = CommerceVerificationMapper.evidence_key(
            transfer.model_copy(update={"cluster": SolanaCluster.DEVNET}),
        )

        assert mainnet_key != devnet_key
        assert mainnet_key.startswith("mainnet-beta:")
        assert devnet_key.startswith("devnet:")

    @pytest.mark.asyncio
    async def test_duplicate_finalized_payment_is_audited_without_second_product_effect(
        self,
        now,
        native_settlement,
    ) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        runtime = FakePaymentRuntime()
        service = SolanaSettlementService(runtime, FrozenClock(now))
        async with factory() as session, session.begin():
            _, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.PAID,
            )
            primary_signature = str(Signature.new_unique())
            intent.verified_signature = primary_signature
            capability = SolanaIntentCapabilityORM(
                public_id=uuid4(),
                intent_id=intent.id,
                secret_sha256="b" * 64,
                expires_at=now + timedelta(minutes=5),
                created_at=now,
            )
            session.add(capability)
            await session.flush()
            issuance_public_id = uuid4()
            session.add(
                SolanaTransactionIssuanceORM(
                    public_id=issuance_public_id,
                    intent_id=intent.id,
                    capability_id=capability.id,
                    payer=str(Pubkey.new_unique()),
                    recent_blockhash=str(Pubkey.new_unique()),
                    last_valid_block_height=1000,
                    issued_context_slot=800,
                    message_sha256="c" * 64,
                    transaction_version=TransactionVersion.V0.value,
                    issued_at=now,
                    accepts_until=now + timedelta(minutes=5),
                ),
            )
            duplicate_signature = str(Signature.new_unique())
            result = VerificationResult(
                disposition=VerificationDisposition.MATCH,
                commitment=SolanaCommitment.FINALIZED,
                transfer=VerifiedTransfer(
                    cluster=native_settlement.cluster,
                    signature=duplicate_signature,
                    issuance_public_id=issuance_public_id,
                    instruction_index=1,
                    source_account=str(Pubkey.new_unique()),
                    destination_account=native_settlement.recipient_wallet,
                    gross_raw_amount=native_settlement.expected_raw_amount,
                    net_raw_amount=native_settlement.expected_raw_amount,
                    slot=901,
                    block_time=now,
                    commitment=SolanaCommitment.FINALIZED,
                    transaction_version=TransactionVersion.V0,
                    detection_source="reference_scan",
                    evidence_sha256="d" * 64,
                ),
            )

            assert await service.record_duplicate_payment(session, intent, result) is True
            assert await service.record_duplicate_payment(session, intent, result) is False
            event_count = await session.scalar(
                select(func.count()).select_from(SolanaPaymentEventORM).where(
                    SolanaPaymentEventORM.event_type == "solana.payment.duplicate_payment",
                ),
            )
            transfer_count = await session.scalar(select(func.count()).select_from(SolanaTransferORM))

            assert intent.state == SolanaIntentState.PAID.value
            assert intent.verified_signature == primary_signature
            assert runtime.results == []
            assert event_count == 1
            assert transfer_count == 1
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_primary_match_schedules_bounded_paid_audit(self, now, native_settlement) -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(CommerceBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        runtime = FakePaymentRuntime()
        clock = FrozenClock(now)
        store = SqlAlchemyReconciliationStore(
            factory,
            SolanaSettlementService(runtime, clock),
            clock,
            max_issuances_per_intent=16,
            retry_delay=timedelta(seconds=5),
        )
        async with factory() as session, session.begin():
            _, intent = await add_payment_and_intent(
                session,
                now,
                native_settlement,
                SolanaIntentState.WAITING,
            )
            capability = SolanaIntentCapabilityORM(
                public_id=uuid4(),
                intent_id=intent.id,
                secret_sha256="e" * 64,
                expires_at=now + timedelta(minutes=5),
                created_at=now,
            )
            session.add(capability)
            await session.flush()
            issuance_public_id = uuid4()
            payer = str(Pubkey.new_unique())
            session.add(
                SolanaTransactionIssuanceORM(
                    public_id=issuance_public_id,
                    intent_id=intent.id,
                    capability_id=capability.id,
                    payer=payer,
                    recent_blockhash=str(Pubkey.new_unique()),
                    last_valid_block_height=1000,
                    issued_context_slot=800,
                    message_sha256="f" * 64,
                    transaction_version=TransactionVersion.V0.value,
                    issued_at=now,
                    accepts_until=now + timedelta(minutes=5),
                ),
            )
            intent_public_id = intent.public_id
        signature = str(Signature.new_unique())
        result = VerificationResult(
            disposition=VerificationDisposition.MATCH,
            commitment=SolanaCommitment.FINALIZED,
            transfer=VerifiedTransfer(
                cluster=native_settlement.cluster,
                signature=signature,
                issuance_public_id=issuance_public_id,
                instruction_index=1,
                source_account=payer,
                destination_account=native_settlement.recipient_wallet,
                gross_raw_amount=native_settlement.expected_raw_amount,
                net_raw_amount=native_settlement.expected_raw_amount,
                slot=902,
                block_time=now,
                commitment=SolanaCommitment.FINALIZED,
                transaction_version=TransactionVersion.V0,
                detection_source="reference_scan",
                evidence_sha256="1" * 64,
            ),
        )

        await store.apply(intent_public_id, result)

        claimed = await store.list_pending(limit=10, now=now + timedelta(seconds=5))
        assert len(claimed) == 1
        assert claimed[0].state == SolanaIntentState.PAID
        assert claimed[0].verified_signature == signature
        assert runtime.results[-1].state == PaymentState.PAID
        await engine.dispose()
