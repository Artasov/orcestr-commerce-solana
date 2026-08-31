from __future__ import annotations

import base64
import hashlib
from datetime import UTC
from uuid import UUID

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from orcestr_commerce_solana.constants import MEMO_PROGRAM_ID, TOKEN_2022_PROGRAM_ID
from orcestr_commerce_solana.errors import SolanaCommerceError, SolanaErrorCode
from orcestr_commerce_solana.rpc.protocol import SolanaRpc
from orcestr_commerce_solana.schemas.assets import SolanaAssetKind, SolanaCommitment
from orcestr_commerce_solana.schemas.transactions import (
    TransactionBuildRequest,
    TransactionIssuanceSnapshot,
    TransactionVersion,
    UnsignedTransactionDTO,
)
from orcestr_commerce_solana.services.assets.codec import SolanaAddressCodec, Token2022Codec


class SolanaTransactionBuilder:
    """Builds minimal v0 transfers whose exact message digest is persisted."""

    transfer_checked_opcode = 12

    def __init__(self, rpc: SolanaRpc) -> None:
        self.rpc = rpc

    async def build(self, request: TransactionBuildRequest) -> UnsignedTransactionDTO:
        """Builds one unsigned transfer transaction for wallet review and signing."""
        issued_at = request.issued_at.astimezone(UTC)
        accepts_until = request.accepts_until.astimezone(UTC)
        if issued_at >= accepts_until:
            raise SolanaCommerceError(SolanaErrorCode.INTENT_EXPIRED, "Transaction acceptance window has expired.")

        latest = await self.rpc.get_latest_blockhash(SolanaCommitment.FINALIZED)
        payer = SolanaAddressCodec.parse(request.payer)
        instructions: list[Instruction] = [
            self._memo_instruction(payer, f"orcestr-issuance:{request.issuance_public_id}"),
        ]
        if request.settlement.memo:
            instructions.append(self._memo_instruction(payer, request.settlement.memo))
        if request.settlement.kind == SolanaAssetKind.NATIVE:
            instructions.append(self._native_instruction(request))
        else:
            instructions.append(self._token_instruction(request))

        message = MessageV0.try_compile(payer, instructions, [], Hash.from_string(latest.blockhash))
        message_sha256 = hashlib.sha256(bytes(message)).hexdigest()
        transaction = VersionedTransaction.populate(message, [Signature.default()])
        issuance = TransactionIssuanceSnapshot(
            public_id=request.issuance_public_id,
            payer=request.payer,
            recent_blockhash=latest.blockhash,
            last_valid_block_height=latest.last_valid_block_height,
            issued_context_slot=latest.context_slot,
            message_sha256=message_sha256,
            version=TransactionVersion.V0,
            issued_at=issued_at,
            accepts_until=accepts_until,
        )
        return UnsignedTransactionDTO(
            transaction=base64.b64encode(bytes(transaction)).decode("ascii"),
            message=f"Pay {request.settlement.display_amount} to {request.settlement.recipient_wallet}",
            issuance=issuance,
        )

    def _native_instruction(self, request: TransactionBuildRequest) -> Instruction:
        """Creates a System Program transfer with reference in the same instruction."""
        settlement = request.settlement
        base = transfer(
            TransferParams(
                from_pubkey=SolanaAddressCodec.parse(request.payer),
                to_pubkey=SolanaAddressCodec.parse(settlement.recipient_wallet),
                lamports=int(settlement.expected_raw_amount),
            ),
        )
        accounts = [*base.accounts, AccountMeta(SolanaAddressCodec.parse(settlement.reference), False, False)]
        return Instruction(base.program_id, base.data, accounts)

    def _token_instruction(self, request: TransactionBuildRequest) -> Instruction:
        """Creates a direct Token-2022 TransferChecked with no CPI or ATA creation."""
        settlement = request.settlement
        if settlement.mint is None or settlement.recipient_token_account is None:
            raise SolanaCommerceError(SolanaErrorCode.INVALID_CONFIGURATION, "Token settlement snapshot is incomplete.")
        source = Token2022Codec.derive_associated_token_account(request.payer, settlement.mint)
        data = (
            bytes([self.transfer_checked_opcode])
            + int(settlement.expected_raw_amount).to_bytes(8, "little")
            + bytes([settlement.decimals])
        )
        accounts = [
            AccountMeta(SolanaAddressCodec.parse(source), False, True),
            AccountMeta(SolanaAddressCodec.parse(settlement.mint), False, False),
            AccountMeta(SolanaAddressCodec.parse(settlement.recipient_token_account), False, True),
            AccountMeta(SolanaAddressCodec.parse(request.payer), True, False),
            AccountMeta(SolanaAddressCodec.parse(settlement.reference), False, False),
        ]
        return Instruction(Pubkey.from_string(TOKEN_2022_PROGRAM_ID), data, accounts)

    @staticmethod
    def _memo_instruction(payer: Pubkey, memo: str) -> Instruction:
        """Creates a top-level signed memo immediately before the payment transfer."""
        return Instruction(
            Pubkey.from_string(MEMO_PROGRAM_ID),
            memo.encode("utf-8"),
            [AccountMeta(payer, True, False)],
        )
