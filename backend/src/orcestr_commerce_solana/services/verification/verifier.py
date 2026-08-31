from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from solders.message import MessageV0
from solders.transaction import VersionedTransaction

from orcestr_commerce_solana.constants import (
    COMPUTE_BUDGET_PROGRAM_ID,
    LEGACY_TOKEN_PROGRAM_ID,
    MEMO_PROGRAM_ID,
    SYSTEM_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
)
from orcestr_commerce_solana.errors import (
    SolanaErrorCode,
    SolanaRpcResponseError,
    SolanaRpcUnavailableError,
)
from orcestr_commerce_solana.rpc.protocol import SolanaRpc, UsedSignatureRegistry
from orcestr_commerce_solana.rpc.types import RpcTransaction
from orcestr_commerce_solana.schemas.assets import SolanaAssetKind, SolanaCommitment
from orcestr_commerce_solana.schemas.transactions import TransactionVersion
from orcestr_commerce_solana.services.assets.codec import Token2022Codec
from orcestr_commerce_solana.services.verification.contracts import (
    VerificationDisposition,
    VerificationRequest,
    VerificationResult,
    VerificationResultFactory,
    VerificationAttemptEvidence,
    VerifiedTransfer,
)


class VerificationMismatch(Exception):
    """Stops raw parsing with one stable reconciliation reason."""

    def __init__(self, code: SolanaErrorCode) -> None:
        super().__init__(code.value)
        self.code = code
        self.linked_to_issuance = False
        self.evidence_sha256: str | None = None


@dataclass(frozen=True)
class PaymentInstruction:
    """Contains the one direct top-level transfer extracted from the message."""

    index: int
    program_id: str
    account_indices: tuple[int, ...]
    data: bytes


@dataclass(frozen=True)
class PreparedTransactionVerification:
    """Caches one signature's RPC material for a single reconciliation pass."""

    signature: str
    preliminary: VerificationResult | None = None
    commitment: SolanaCommitment | None = None
    transaction: RpcTransaction | None = None


class SolanaTransactionVerifier:
    """Verifies raw System or Token-2022 transfers against immutable snapshots."""

    system_transfer_opcode = 2
    transfer_checked_opcode = 12
    allowed_non_payment_programs = frozenset({COMPUTE_BUDGET_PROGRAM_ID, MEMO_PROGRAM_ID})

    def __init__(
        self,
        rpc: SolanaRpc,
        used_signatures: UsedSignatureRegistry,
    ) -> None:
        self.rpc = rpc
        self.used_signatures = used_signatures

    async def prepare(self, signature: str) -> PreparedTransactionVerification:
        """Fetches status and raw transaction once for all persisted issuances."""
        try:
            await self.rpc.check_cluster()
            status = await self.rpc.get_signature_status(signature)
            if status is None:
                return PreparedTransactionVerification(
                    signature=signature,
                    preliminary=VerificationResultFactory.unknown(SolanaErrorCode.TRANSACTION_NOT_FOUND.value),
                )
            commitment = self._read_commitment(status.confirmation_status)
            if commitment is None:
                return PreparedTransactionVerification(
                    signature=signature,
                    preliminary=VerificationResult(
                        disposition=VerificationDisposition.OBSERVED,
                        reason_code=SolanaErrorCode.TRANSACTION_NOT_FINAL.value,
                        retryable=True,
                    ),
                )
            transaction = await self.rpc.get_transaction(signature, commitment)
            if transaction is None:
                return PreparedTransactionVerification(
                    signature=signature,
                    preliminary=VerificationResultFactory.unknown(SolanaErrorCode.TRANSACTION_NOT_FOUND.value),
                )
            return PreparedTransactionVerification(
                signature=signature,
                commitment=commitment,
                transaction=transaction,
            )
        except SolanaRpcUnavailableError:
            return PreparedTransactionVerification(
                signature=signature,
                preliminary=VerificationResultFactory.unknown(
                    SolanaErrorCode.RPC_TEMPORARILY_UNAVAILABLE.value,
                ),
            )
        except SolanaRpcResponseError as exc:
            preliminary = (
                VerificationResultFactory.review(exc.code.value)
                if exc.code == SolanaErrorCode.WRONG_CLUSTER
                else VerificationResultFactory.unknown(exc.code.value)
            )
            return PreparedTransactionVerification(signature=signature, preliminary=preliminary)

    async def verify(
        self,
        request: VerificationRequest,
        *,
        prepared: PreparedTransactionVerification | None = None,
    ) -> VerificationResult:
        """Returns MATCH only after strict raw parsing at the required commitment."""
        material = prepared or await self.prepare(request.signature)
        if material.signature != request.signature:
            raise ValueError("Prepared verification signature does not match the request.")
        if material.preliminary is not None:
            return material.preliminary
        if material.commitment is None or material.transaction is None:
            raise ValueError("Prepared verification material is incomplete.")
        commitment = material.commitment
        transaction = material.transaction
        try:
            transfer = self._verify_raw(request, transaction, commitment)
            time_reason = self._time_policy_reason(request, transaction)
            if time_reason is not None:
                return VerificationResult(
                    disposition=VerificationDisposition.REVIEW,
                    reason_code=time_reason,
                    commitment=commitment,
                    transfer=transfer,
                )
            if await self.used_signatures.is_used(
                request.settlement.cluster,
                request.signature,
                request.intent_public_id,
            ):
                return VerificationResult(
                    disposition=VerificationDisposition.REVIEW,
                    reason_code=SolanaErrorCode.SIGNATURE_ALREADY_USED.value,
                    commitment=commitment,
                    transfer=transfer,
                )
            if commitment.rank < request.settlement.required_commitment.rank:
                return VerificationResult(
                    disposition=VerificationDisposition.CONFIRMED,
                    reason_code=SolanaErrorCode.TRANSACTION_NOT_FINAL.value,
                    retryable=True,
                    commitment=commitment,
                    transfer=transfer,
                )
            return VerificationResult(
                disposition=VerificationDisposition.MATCH,
                commitment=commitment,
                transfer=transfer,
            )
        except VerificationMismatch as exc:
            attempt = None
            if exc.linked_to_issuance and exc.evidence_sha256 is not None:
                attempt = VerificationAttemptEvidence(
                    signature=request.signature,
                    issuance_public_id=request.issuance.public_id,
                    evidence_sha256=exc.evidence_sha256,
                )
            if exc.code == SolanaErrorCode.TRANSACTION_FAILED:
                return VerificationResult(
                    disposition=VerificationDisposition.FAILED,
                    reason_code=exc.code.value,
                    attempt=attempt,
                )
            return VerificationResult(
                disposition=VerificationDisposition.REVIEW,
                reason_code=exc.code.value,
                attempt=attempt,
            )
        except (ValueError, IndexError, TypeError):
            return VerificationResultFactory.review(SolanaErrorCode.TRANSACTION_DECODE_FAILED.value)

    def _verify_raw(
        self,
        request: VerificationRequest,
        transaction: RpcTransaction,
        commitment: SolanaCommitment,
    ) -> VerifiedTransfer:
        """Parses compiled instruction bytes and verifies exact balance deltas."""
        parsed = VersionedTransaction.from_bytes(transaction.raw_transaction)
        signatures = tuple(str(signature) for signature in parsed.signatures)
        if len(signatures) != 1 or signatures[0] != request.signature:
            raise VerificationMismatch(SolanaErrorCode.SIGNATURE_MISMATCH)
        if parsed.verify_with_results() != [True]:
            raise VerificationMismatch(SolanaErrorCode.SIGNATURE_MISMATCH)

        message = parsed.message
        version = TransactionVersion.V0 if isinstance(message, MessageV0) else TransactionVersion.LEGACY
        if version != request.issuance.version:
            raise VerificationMismatch(SolanaErrorCode.ISSUANCE_MISMATCH)
        if str(message.recent_blockhash) != request.issuance.recent_blockhash:
            raise VerificationMismatch(SolanaErrorCode.ISSUANCE_MISMATCH)
        message_sha256 = hashlib.sha256(bytes(message)).hexdigest()
        if message_sha256 != request.issuance.message_sha256:
            raise VerificationMismatch(SolanaErrorCode.ISSUANCE_MISMATCH)
        evidence_sha256 = hashlib.sha256(transaction.raw_transaction).hexdigest()
        try:
            if transaction.meta.get("err") is not None:
                raise VerificationMismatch(SolanaErrorCode.TRANSACTION_FAILED)
            raw_inner = transaction.meta.get("innerInstructions")
            if raw_inner not in (None, []):
                raise VerificationMismatch(SolanaErrorCode.UNSUPPORTED_CPI_OR_SWAP)

            account_keys = self._account_keys(parsed, transaction.meta)
            payment = self._find_payment_instruction(message.instructions, account_keys)
            settlement = request.settlement
            if settlement.kind == SolanaAssetKind.NATIVE:
                source, destination, gross, net = self._verify_native(request, transaction, payment, account_keys)
                mint = None
                token_program = None
            else:
                source, destination, gross, net = self._verify_token(request, transaction, payment, account_keys)
                mint = settlement.mint
                token_program = TOKEN_2022_PROGRAM_ID
        except VerificationMismatch as exc:
            exc.linked_to_issuance = True
            exc.evidence_sha256 = evidence_sha256
            raise
        return VerifiedTransfer(
            cluster=request.settlement.cluster,
            signature=request.signature,
            issuance_public_id=request.issuance.public_id,
            instruction_index=payment.index,
            source_account=source,
            destination_account=destination,
            mint=mint,
            token_program=token_program,
            gross_raw_amount=str(gross),
            net_raw_amount=str(net),
            slot=transaction.slot,
            block_time=transaction.block_time,
            commitment=commitment,
            transaction_version=version,
            detection_source=request.detection_source,
            evidence_sha256=evidence_sha256,
        )

    @staticmethod
    def _time_policy_reason(request: VerificationRequest, transaction: RpcTransaction) -> str | None:
        """Applies the acceptance window only after the raw transfer is normalized."""
        if transaction.block_time is not None and transaction.block_time > request.issuance.accepts_until:
            return SolanaErrorCode.LATE_PAYMENT.value
        if transaction.block_time is None and request.now > request.issuance.accepts_until:
            return SolanaErrorCode.BLOCK_TIME_UNAVAILABLE.value
        return None

    def _find_payment_instruction(self, instructions: Any, account_keys: tuple[str, ...]) -> PaymentInstruction:
        """Allows one direct payment plus optional top-level compute budget and memo."""
        payments: list[PaymentInstruction] = []
        for index, instruction in enumerate(instructions):
            program_index = int(instruction.program_id_index)
            if program_index >= len(account_keys):
                raise VerificationMismatch(SolanaErrorCode.TRANSACTION_DECODE_FAILED)
            program_id = account_keys[program_index]
            if program_id == LEGACY_TOKEN_PROGRAM_ID:
                raise VerificationMismatch(SolanaErrorCode.LEGACY_TOKEN_PROGRAM)
            if program_id in {SYSTEM_PROGRAM_ID, TOKEN_2022_PROGRAM_ID}:
                payments.append(
                    PaymentInstruction(
                        index=index,
                        program_id=program_id,
                        account_indices=tuple(int(value) for value in instruction.accounts),
                        data=bytes(instruction.data),
                    ),
                )
                continue
            if program_id not in self.allowed_non_payment_programs:
                raise VerificationMismatch(SolanaErrorCode.UNSUPPORTED_INSTRUCTION)
        if len(payments) != 1:
            raise VerificationMismatch(SolanaErrorCode.MULTIPLE_PAYMENT_INSTRUCTIONS)
        return payments[0]

    def _verify_native(
        self,
        request: VerificationRequest,
        transaction: RpcTransaction,
        payment: PaymentInstruction,
        account_keys: tuple[str, ...],
    ) -> tuple[str, str, int, int]:
        """Accepts only an exact System transfer and recipient lamport delta."""
        if payment.program_id != SYSTEM_PROGRAM_ID:
            raise VerificationMismatch(SolanaErrorCode.WRONG_PROGRAM)
        if len(payment.data) != 12 or int.from_bytes(payment.data[0:4], "little") != self.system_transfer_opcode:
            raise VerificationMismatch(SolanaErrorCode.UNSUPPORTED_INSTRUCTION)
        if len(payment.account_indices) != 3:
            raise VerificationMismatch(SolanaErrorCode.REFERENCE_MISSING)
        source_index, destination_index, reference_index = payment.account_indices
        source = self._key(account_keys, source_index)
        destination = self._key(account_keys, destination_index)
        reference = self._key(account_keys, reference_index)
        if source != request.issuance.payer:
            raise VerificationMismatch(SolanaErrorCode.ISSUANCE_MISMATCH)
        if destination != request.settlement.recipient_wallet:
            raise VerificationMismatch(SolanaErrorCode.WRONG_RECIPIENT)
        if reference != request.settlement.reference:
            raise VerificationMismatch(SolanaErrorCode.REFERENCE_MISSING)
        amount = int.from_bytes(payment.data[4:12], "little")
        expected = int(request.settlement.expected_raw_amount)
        if amount != expected:
            raise VerificationMismatch(SolanaErrorCode.WRONG_AMOUNT)
        pre = self._balance(transaction.meta, "preBalances", destination_index)
        post = self._balance(transaction.meta, "postBalances", destination_index)
        net = post - pre
        if net != expected:
            raise VerificationMismatch(SolanaErrorCode.INVALID_BALANCE_DELTA)
        return source, destination, amount, net

    def _verify_token(
        self,
        request: VerificationRequest,
        transaction: RpcTransaction,
        payment: PaymentInstruction,
        account_keys: tuple[str, ...],
    ) -> tuple[str, str, int, int]:
        """Accepts only direct Token-2022 TransferChecked with exact token deltas."""
        settlement = request.settlement
        if payment.program_id != TOKEN_2022_PROGRAM_ID:
            raise VerificationMismatch(SolanaErrorCode.WRONG_PROGRAM)
        if len(payment.data) != 10 or payment.data[0] != self.transfer_checked_opcode:
            raise VerificationMismatch(SolanaErrorCode.UNSUPPORTED_INSTRUCTION)
        if len(payment.account_indices) != 5:
            raise VerificationMismatch(SolanaErrorCode.REFERENCE_MISSING)
        source_index, mint_index, destination_index, authority_index, reference_index = payment.account_indices
        source = self._key(account_keys, source_index)
        mint = self._key(account_keys, mint_index)
        destination = self._key(account_keys, destination_index)
        authority = self._key(account_keys, authority_index)
        reference = self._key(account_keys, reference_index)
        if settlement.mint is None or settlement.recipient_token_account is None:
            raise VerificationMismatch(SolanaErrorCode.WRONG_MINT)
        expected_source = Token2022Codec.derive_associated_token_account(request.issuance.payer, settlement.mint)
        if source != expected_source or authority != request.issuance.payer:
            raise VerificationMismatch(SolanaErrorCode.ISSUANCE_MISMATCH)
        if mint != settlement.mint:
            raise VerificationMismatch(SolanaErrorCode.WRONG_MINT)
        if destination != settlement.recipient_token_account:
            raise VerificationMismatch(SolanaErrorCode.WRONG_RECIPIENT)
        if reference != settlement.reference:
            raise VerificationMismatch(SolanaErrorCode.REFERENCE_MISSING)
        amount = int.from_bytes(payment.data[1:9], "little")
        if amount != int(settlement.expected_raw_amount):
            raise VerificationMismatch(SolanaErrorCode.WRONG_AMOUNT)
        if payment.data[9] != settlement.decimals:
            raise VerificationMismatch(SolanaErrorCode.WRONG_DECIMALS_IN_INSTRUCTION)
        source_pre = self._token_balance(transaction.meta, "preTokenBalances", source_index, mint, settlement.decimals)
        source_post = self._token_balance(transaction.meta, "postTokenBalances", source_index, mint, settlement.decimals)
        destination_pre = self._token_balance(
            transaction.meta,
            "preTokenBalances",
            destination_index,
            mint,
            settlement.decimals,
        )
        destination_post = self._token_balance(
            transaction.meta,
            "postTokenBalances",
            destination_index,
            mint,
            settlement.decimals,
        )
        if source_pre - source_post != amount or destination_post - destination_pre != amount:
            raise VerificationMismatch(SolanaErrorCode.INVALID_BALANCE_DELTA)
        return source, destination, amount, destination_post - destination_pre

    @staticmethod
    def _account_keys(parsed: VersionedTransaction, meta: dict[str, Any]) -> tuple[str, ...]:
        """Resolves static and loaded v0 account keys in compiled-index order."""
        keys = [str(key) for key in parsed.message.account_keys]
        loaded = meta.get("loadedAddresses")
        if loaded is None:
            return tuple(keys)
        if not isinstance(loaded, dict):
            raise VerificationMismatch(SolanaErrorCode.TRANSACTION_DECODE_FAILED)
        writable = loaded.get("writable", [])
        readonly = loaded.get("readonly", [])
        if not isinstance(writable, list) or not isinstance(readonly, list):
            raise VerificationMismatch(SolanaErrorCode.TRANSACTION_DECODE_FAILED)
        if not all(isinstance(value, str) for value in [*writable, *readonly]):
            raise VerificationMismatch(SolanaErrorCode.TRANSACTION_DECODE_FAILED)
        return tuple([*keys, *writable, *readonly])

    @staticmethod
    def _key(account_keys: tuple[str, ...], index: int) -> str:
        """Reads one compiled account index with explicit bounds checking."""
        if index < 0 or index >= len(account_keys):
            raise VerificationMismatch(SolanaErrorCode.TRANSACTION_DECODE_FAILED)
        return account_keys[index]

    @staticmethod
    def _balance(meta: dict[str, Any], field: str, index: int) -> int:
        """Reads one exact lamport balance from transaction metadata."""
        balances = meta.get(field)
        if not isinstance(balances, list) or index >= len(balances):
            raise VerificationMismatch(SolanaErrorCode.TRANSACTION_DECODE_FAILED)
        value = balances[index]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise VerificationMismatch(SolanaErrorCode.TRANSACTION_DECODE_FAILED)
        return value

    @staticmethod
    def _token_balance(
        meta: dict[str, Any],
        field: str,
        account_index: int,
        mint: str,
        decimals: int,
    ) -> int:
        """Reads raw token units for one exact account, mint, program, and decimals."""
        balances = meta.get(field)
        if not isinstance(balances, list):
            raise VerificationMismatch(SolanaErrorCode.TRANSACTION_DECODE_FAILED)
        matches = [
            item
            for item in balances
            if isinstance(item, dict) and item.get("accountIndex") == account_index
        ]
        if len(matches) != 1:
            raise VerificationMismatch(SolanaErrorCode.INVALID_BALANCE_DELTA)
        item = matches[0]
        if item.get("mint") != mint:
            raise VerificationMismatch(SolanaErrorCode.WRONG_MINT)
        program_id = item.get("programId")
        if program_id == LEGACY_TOKEN_PROGRAM_ID:
            raise VerificationMismatch(SolanaErrorCode.LEGACY_TOKEN_PROGRAM)
        if program_id is not None and program_id != TOKEN_2022_PROGRAM_ID:
            raise VerificationMismatch(SolanaErrorCode.WRONG_PROGRAM)
        ui_amount = item.get("uiTokenAmount")
        if not isinstance(ui_amount, dict) or ui_amount.get("decimals") != decimals:
            raise VerificationMismatch(SolanaErrorCode.WRONG_DECIMALS_IN_INSTRUCTION)
        amount = ui_amount.get("amount")
        if not isinstance(amount, str) or not amount.isdigit():
            raise VerificationMismatch(SolanaErrorCode.TRANSACTION_DECODE_FAILED)
        return int(amount)

    @staticmethod
    def _read_commitment(value: str | None) -> SolanaCommitment | None:
        """Maps RPC status while treating processed as provisional observation."""
        if value == SolanaCommitment.FINALIZED.value:
            return SolanaCommitment.FINALIZED
        if value == SolanaCommitment.CONFIRMED.value:
            return SolanaCommitment.CONFIRMED
        return None
