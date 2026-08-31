from __future__ import annotations

import base64
from datetime import timedelta
from uuid import uuid4

import pytest
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from conftest import FakeRpc, FixtureLoader, MemoryUsedSignatures, TransactionFixtureFactory
from orcestr_commerce_solana.constants import LEGACY_TOKEN_PROGRAM_ID, SYSTEM_PROGRAM_ID, TOKEN_2022_PROGRAM_ID
from orcestr_commerce_solana.errors import SolanaErrorCode, SolanaRpcUnavailableError
from orcestr_commerce_solana.rpc.types import RpcSignatureStatus, RpcTransaction
from orcestr_commerce_solana.schemas.assets import SettlementSnapshot, SolanaCommitment
from orcestr_commerce_solana.schemas.transactions import TransactionBuildRequest, TransactionIssuanceSnapshot, TransactionVersion
from orcestr_commerce_solana.services.assets.codec import Token2022Codec
from orcestr_commerce_solana.services.intents.builder import SolanaTransactionBuilder
from orcestr_commerce_solana.services.verification.contracts import (
    VerificationDisposition,
    VerificationRequest,
)
from orcestr_commerce_solana.services.verification.verifier import SolanaTransactionVerifier


class UnavailableRpc(FakeRpc):
    """Fails cluster checks like a rate-limited public endpoint."""

    async def check_cluster(self) -> str:
        raise SolanaRpcUnavailableError(
            SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE,
            "rate limited",
        )


class TestSolanaTransactionBuilder:
    """Freezes exact direct transfer messages and reference account placement."""

    @pytest.mark.asyncio
    async def test_native_transfer_has_reference_in_payment_instruction(
        self,
        now,
        native_settlement: SettlementSnapshot,
    ) -> None:
        rpc = FakeRpc()
        payer = str(Pubkey.new_unique())
        result = await SolanaTransactionBuilder(rpc).build(
            TransactionBuildRequest(
                issuance_public_id=uuid4(),
                payer=payer,
                settlement=native_settlement,
                issued_at=now,
                accepts_until=now + timedelta(minutes=10),
            ),
        )
        transaction = VersionedTransaction.from_bytes(base64.b64decode(result.transaction))
        payment = transaction.message.instructions[-1]
        keys = [str(key) for key in transaction.message.account_keys]

        assert keys[payment.program_id_index] == SYSTEM_PROGRAM_ID
        assert len(payment.accounts) == 3
        assert keys[payment.accounts[-1]] == native_settlement.reference
        assert int.from_bytes(bytes(payment.data)[4:12], "little") == int(native_settlement.expected_raw_amount)
        assert result.issuance.message_sha256 == __import__("hashlib").sha256(bytes(transaction.message)).hexdigest()
        assert result.issuance.issued_context_slot == rpc.latest.context_slot
        assert len(base64.b64decode(result.transaction)) <= 1232

    @pytest.mark.asyncio
    async def test_token_transfer_checked_has_reference_and_exact_decimals(
        self,
        now,
        token_settlement: SettlementSnapshot,
    ) -> None:
        rpc = FakeRpc()
        payer = str(Pubkey.new_unique())
        result = await SolanaTransactionBuilder(rpc).build(
            TransactionBuildRequest(
                issuance_public_id=uuid4(),
                payer=payer,
                settlement=token_settlement,
                issued_at=now,
                accepts_until=now + timedelta(minutes=10),
            ),
        )
        transaction = VersionedTransaction.from_bytes(base64.b64decode(result.transaction))
        payment = transaction.message.instructions[-1]
        keys = [str(key) for key in transaction.message.account_keys]
        data = bytes(payment.data)

        assert keys[payment.program_id_index] == TOKEN_2022_PROGRAM_ID
        assert len(payment.accounts) == 5
        assert keys[payment.accounts[-1]] == token_settlement.reference
        assert data[0] == 12
        assert int.from_bytes(data[1:9], "little") == int(token_settlement.expected_raw_amount)
        assert data[9] == token_settlement.decimals
        assert len(base64.b64decode(result.transaction)) <= 1232


class TestSolanaTransactionVerifier:
    """Covers finality, exact deltas, CPI denial, Legacy denial, and uniqueness."""

    @pytest.mark.asyncio
    async def test_native_finalized_exact_payment_matches(
        self,
        now,
        native_settlement: SettlementSnapshot,
    ) -> None:
        rpc, request = await self._native_request(now, native_settlement)

        result = await SolanaTransactionVerifier(rpc, MemoryUsedSignatures()).verify(request)

        assert result.disposition == VerificationDisposition.MATCH
        assert result.transfer is not None
        assert result.transfer.net_raw_amount == native_settlement.expected_raw_amount
        assert result.transfer.instruction_index == 2

    @pytest.mark.asyncio
    async def test_confirmed_then_finalized_uses_same_issued_message(
        self,
        now,
        native_settlement: SettlementSnapshot,
    ) -> None:
        rpc, request = await self._native_request(now, native_settlement)
        rpc.status = RpcSignatureStatus(slot=10, confirmation_status="confirmed")
        verifier = SolanaTransactionVerifier(rpc, MemoryUsedSignatures())

        confirmed = await verifier.verify(request)
        rpc.status = RpcSignatureStatus(slot=10, confirmation_status="finalized")
        finalized = await verifier.verify(request)

        assert confirmed.disposition == VerificationDisposition.CONFIRMED
        assert confirmed.retryable is True
        assert confirmed.transfer is not None
        assert finalized.disposition == VerificationDisposition.MATCH
        assert finalized.transfer is not None
        assert confirmed.transfer.evidence_sha256 == finalized.transfer.evidence_sha256

    @pytest.mark.asyncio
    async def test_token_2022_finalized_exact_payment_matches(
        self,
        now,
        token_settlement: SettlementSnapshot,
    ) -> None:
        rpc = FakeRpc()
        payer_keypair = Keypair()
        payer = str(payer_keypair.pubkey())
        built = await SolanaTransactionBuilder(rpc).build(
            TransactionBuildRequest(
                issuance_public_id=uuid4(),
                payer=payer,
                settlement=token_settlement,
                issued_at=now,
                accepts_until=now + timedelta(minutes=10),
            ),
        )
        raw, signature, signed = TransactionFixtureFactory.sign(built.transaction, payer_keypair)
        source = Token2022Codec.derive_associated_token_account(payer, token_settlement.mint or "")
        rpc.status = RpcSignatureStatus(slot=11, confirmation_status="finalized")
        rpc.transaction = RpcTransaction(
            slot=11,
            block_time=now + timedelta(seconds=2),
            raw_transaction=raw,
            meta=TransactionFixtureFactory.token_meta(
                signed,
                source,
                token_settlement.recipient_token_account or "",
                token_settlement.mint or "",
                token_settlement.decimals,
                int(token_settlement.expected_raw_amount),
            ),
        )
        request = VerificationRequest(
            signature=signature,
            intent_public_id=uuid4(),
            settlement=token_settlement,
            issuance=built.issuance,
            now=now + timedelta(seconds=3),
        )

        result = await SolanaTransactionVerifier(rpc, MemoryUsedSignatures()).verify(request)

        assert result.disposition == VerificationDisposition.MATCH
        assert result.transfer is not None
        assert result.transfer.mint == token_settlement.mint
        assert result.transfer.token_program == TOKEN_2022_PROGRAM_ID

    @pytest.mark.asyncio
    async def test_issued_message_with_inner_instruction_is_rejected_as_cpi(
        self,
        now,
        native_settlement: SettlementSnapshot,
    ) -> None:
        rpc, request = await self._native_request(now, native_settlement)
        fixture = FixtureLoader.load("negative_cpi_swap.json")
        assert rpc.transaction is not None
        rpc.transaction = rpc.transaction.model_copy(
            update={"meta": {**rpc.transaction.meta, **fixture["meta"]}},
        )

        result = await SolanaTransactionVerifier(rpc, MemoryUsedSignatures()).verify(request)

        assert result.disposition == VerificationDisposition.REVIEW
        assert result.reason_code == fixture["expected_reason"]

    @pytest.mark.asyncio
    async def test_embedded_but_invalid_signature_is_rejected(
        self,
        now,
        native_settlement: SettlementSnapshot,
    ) -> None:
        rpc, request = await self._native_request(now, native_settlement)
        assert rpc.transaction is not None
        parsed = VersionedTransaction.from_bytes(rpc.transaction.raw_transaction)
        invalid_signature = Signature.new_unique()
        rpc.transaction = rpc.transaction.model_copy(
            update={
                "raw_transaction": bytes(
                    VersionedTransaction.populate(parsed.message, [invalid_signature]),
                ),
            },
        )

        result = await SolanaTransactionVerifier(rpc, MemoryUsedSignatures()).verify(
            request.model_copy(update={"signature": str(invalid_signature)}),
        )

        assert result.disposition == VerificationDisposition.REVIEW
        assert result.reason_code == SolanaErrorCode.SIGNATURE_MISMATCH.value

    @pytest.mark.asyncio
    async def test_legacy_token_instruction_is_always_rejected(
        self,
        now,
        token_settlement: SettlementSnapshot,
    ) -> None:
        payer_keypair = Keypair()
        payer = payer_keypair.pubkey()
        mint = Pubkey.from_string(token_settlement.mint or "")
        source = Pubkey.from_string(Token2022Codec.derive_associated_token_account(str(payer), str(mint)))
        destination = Pubkey.from_string(token_settlement.recipient_token_account or "")
        reference = Pubkey.from_string(token_settlement.reference)
        data = bytes([12]) + int(token_settlement.expected_raw_amount).to_bytes(8, "little") + bytes([6])
        instruction = Instruction(
            Pubkey.from_string(LEGACY_TOKEN_PROGRAM_ID),
            data,
            [
                AccountMeta(source, False, True),
                AccountMeta(mint, False, False),
                AccountMeta(destination, False, True),
                AccountMeta(payer, True, False),
                AccountMeta(reference, False, False),
            ],
        )
        message = MessageV0.try_compile(payer, [instruction], [], Hash.default())
        transaction = VersionedTransaction(message, [payer_keypair])
        signature = transaction.signatures[0]
        issuance = TransactionIssuanceSnapshot(
            public_id=uuid4(),
            payer=str(payer),
            recent_blockhash=str(message.recent_blockhash),
            last_valid_block_height=1000,
            issued_context_slot=800,
            message_sha256=__import__("hashlib").sha256(bytes(message)).hexdigest(),
            version=TransactionVersion.V0,
            issued_at=now,
            accepts_until=now + timedelta(minutes=10),
        )
        rpc = FakeRpc()
        rpc.status = RpcSignatureStatus(slot=12, confirmation_status="finalized")
        rpc.transaction = RpcTransaction(
            slot=12,
            block_time=now,
            raw_transaction=bytes(transaction),
            meta=TransactionFixtureFactory.token_meta(
                transaction,
                str(source),
                str(destination),
                str(mint),
                6,
                int(token_settlement.expected_raw_amount),
            ),
        )

        result = await SolanaTransactionVerifier(rpc, MemoryUsedSignatures()).verify(
            VerificationRequest(
                signature=str(signature),
                intent_public_id=uuid4(),
                settlement=token_settlement,
                issuance=issuance,
                now=now,
            ),
        )

        assert result.disposition == VerificationDisposition.REVIEW
        assert result.reason_code == SolanaErrorCode.LEGACY_TOKEN_PROGRAM.value

    @pytest.mark.asyncio
    async def test_duplicate_signature_across_intents_is_rejected(
        self,
        now,
        native_settlement: SettlementSnapshot,
    ) -> None:
        rpc, request = await self._native_request(now, native_settlement)

        result = await SolanaTransactionVerifier(
            rpc,
            MemoryUsedSignatures({request.signature}),
        ).verify(request)

        assert result.disposition == VerificationDisposition.REVIEW
        assert result.reason_code == SolanaErrorCode.SIGNATURE_ALREADY_USED.value

    @pytest.mark.asyncio
    async def test_rpc_outage_is_retryable_unknown(self, now, native_settlement: SettlementSnapshot) -> None:
        payer = str(Pubkey.new_unique())
        issuance = TransactionIssuanceSnapshot(
            public_id=uuid4(),
            payer=payer,
            recent_blockhash=str(Hash.default()),
            last_valid_block_height=1,
            issued_context_slot=1,
            message_sha256="0" * 64,
            version=TransactionVersion.V0,
            issued_at=now,
            accepts_until=now + timedelta(minutes=1),
        )

        result = await SolanaTransactionVerifier(
            UnavailableRpc(),
            MemoryUsedSignatures(),
        ).verify(
            VerificationRequest(
                signature=str(Signature.new_unique()),
                intent_public_id=uuid4(),
                settlement=native_settlement,
                issuance=issuance,
                now=now,
            ),
        )

        assert result.disposition == VerificationDisposition.UNKNOWN
        assert result.retryable is True
        assert result.reason_code == SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE.value

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("block_time_mode", "expected_reason"),
        (
            ("late", SolanaErrorCode.LATE_PAYMENT.value),
            ("missing", SolanaErrorCode.BLOCK_TIME_UNAVAILABLE.value),
        ),
    )
    async def test_finalized_exact_transfer_applies_time_policy_after_normalization(
        self,
        now,
        native_settlement,
        block_time_mode,
        expected_reason,
    ) -> None:
        rpc, request = await self._native_request(now, native_settlement)
        assert rpc.transaction is not None
        block_time = (
            request.issuance.accepts_until + timedelta(seconds=1)
            if block_time_mode == "late"
            else None
        )
        rpc.transaction = rpc.transaction.model_copy(update={"block_time": block_time})
        request = request.model_copy(update={"now": request.issuance.accepts_until + timedelta(seconds=2)})

        result = await SolanaTransactionVerifier(rpc, MemoryUsedSignatures()).verify(request)

        assert result.disposition == VerificationDisposition.REVIEW
        assert result.reason_code == expected_reason
        assert result.transfer is not None
        assert result.transfer.commitment == SolanaCommitment.FINALIZED
        assert result.transfer.signature == request.signature

    @pytest.mark.asyncio
    async def test_prepared_rpc_material_is_reused_across_issuances(
        self,
        now,
        native_settlement,
    ) -> None:
        class CountingRpc(FakeRpc):
            def __init__(self) -> None:
                super().__init__()
                self.calls = {"cluster": 0, "status": 0, "transaction": 0}

            async def check_cluster(self):
                self.calls["cluster"] += 1
                return await super().check_cluster()

            async def get_signature_status(self, signature):
                self.calls["status"] += 1
                return await super().get_signature_status(signature)

            async def get_transaction(self, signature, commitment):
                self.calls["transaction"] += 1
                return await super().get_transaction(signature, commitment)

        original_rpc, request = await self._native_request(now, native_settlement)
        rpc = CountingRpc()
        rpc.status = original_rpc.status
        rpc.transaction = original_rpc.transaction
        verifier = SolanaTransactionVerifier(rpc, MemoryUsedSignatures())
        prepared = await verifier.prepare(request.signature)

        mismatch = await verifier.verify(
            request.model_copy(
                update={
                    "issuance": request.issuance.model_copy(
                        update={"recent_blockhash": str(Pubkey.new_unique())},
                    ),
                },
            ),
            prepared=prepared,
        )
        match = await verifier.verify(request, prepared=prepared)

        assert mismatch.reason_code == SolanaErrorCode.ISSUANCE_MISMATCH.value
        assert match.disposition == VerificationDisposition.MATCH
        assert rpc.calls == {"cluster": 1, "status": 1, "transaction": 1}

    async def _native_request(self, now, settlement: SettlementSnapshot) -> tuple[FakeRpc, VerificationRequest]:
        rpc = FakeRpc()
        payer_keypair = Keypair()
        payer = str(payer_keypair.pubkey())
        built = await SolanaTransactionBuilder(rpc).build(
            TransactionBuildRequest(
                issuance_public_id=uuid4(),
                payer=payer,
                settlement=settlement,
                issued_at=now,
                accepts_until=now + timedelta(minutes=10),
            ),
        )
        raw, signature, signed = TransactionFixtureFactory.sign(built.transaction, payer_keypair)
        rpc.status = RpcSignatureStatus(slot=10, confirmation_status="finalized")
        rpc.transaction = RpcTransaction(
            slot=10,
            block_time=now + timedelta(seconds=1),
            raw_transaction=raw,
            meta=TransactionFixtureFactory.native_meta(
                signed,
                settlement.recipient_wallet,
                int(settlement.expected_raw_amount),
            ),
        )
        return rpc, VerificationRequest(
            signature=signature,
            intent_public_id=uuid4(),
            settlement=settlement,
            issuance=built.issuance,
            now=now + timedelta(seconds=2),
        )
