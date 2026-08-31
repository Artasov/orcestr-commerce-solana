from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

from orcestr_commerce_solana.events import CommercePaymentUpdatedEvent
from orcestr_commerce_solana.models import SolanaPaymentIntentORM, SolanaTransactionIssuanceORM
from orcestr_commerce_solana.schemas.assets import (
    SettlementSnapshot,
    SolanaAssetKind,
    SolanaAssetPolicy,
    ValidatedSolanaAsset,
)
from orcestr_commerce_solana.schemas.intents import SolanaPaymentOptionDTO
from orcestr_commerce_solana.constants import MAINNET_GENESIS_HASH, ORCESTR_TOKEN_MINT, TOKEN_2022_PROGRAM_ID


class TestEventsAndModels:
    """Freezes realtime payload and fail-closed DB metadata invariants."""

    def test_realtime_event_uses_shared_event_discriminator(self) -> None:
        event = CommercePaymentUpdatedEvent(
            order_public_id=uuid4(),
            payment_public_id=uuid4(),
            revision=3,
        )

        assert event.model_dump(mode="json") == {
            "event": "commerce.payment.updated",
            "order_public_id": str(event.order_public_id),
            "payment_public_id": str(event.payment_public_id),
            "revision": 3,
        }

    def test_active_token_cannot_bypass_on_chain_activation_evidence(self) -> None:
        policy = SolanaAssetPolicy(
            option_id="solana_orcestr",
            cluster="mainnet-beta",
            genesis_hash=MAINNET_GENESIS_HASH,
            kind=SolanaAssetKind.TOKEN,
            mint=ORCESTR_TOKEN_MINT,
            token_program=TOKEN_2022_PROGRAM_ID,
            decimals=6,
            display_name="Orcestr",
            symbol="ORCESTR",
        )

        with pytest.raises(ValidationError):
            ValidatedSolanaAsset(policy=policy)

    def test_expected_raw_amount_has_positive_and_u64_db_constraints(self) -> None:
        constraints = {
            constraint.name: str(constraint.sqltext)
            for constraint in SolanaPaymentIntentORM.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }

        assert constraints["ck_commerce_solana_payment_intent_commerce_solana_intent_expected_amount_positive"] == "expected_raw_amount > 0"
        assert "18446744073709551615" in constraints[
            "ck_commerce_solana_payment_intent_commerce_solana_intent_expected_amount_u64"
        ]

    def test_terminal_horizon_and_issuance_slot_are_required_db_facts(self) -> None:
        intent_constraints = {
            constraint.name: str(constraint.sqltext)
            for constraint in SolanaPaymentIntentORM.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        issuance_constraints = {
            constraint.name: str(constraint.sqltext)
            for constraint in SolanaTransactionIssuanceORM.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }

        assert SolanaPaymentIntentORM.__table__.c.reconcile_until.nullable is False
        assert SolanaPaymentIntentORM.__table__.c.reconcile_until.index is True
        assert "reconcile_until > expires_at" in intent_constraints.values()
        assert SolanaTransactionIssuanceORM.__table__.c.issued_context_slot.nullable is False
        assert "issued_context_slot >= 0" in issuance_constraints.values()

    def test_zero_asset_minimum_and_inconsistent_display_snapshot_are_rejected(
        self,
        native_settlement,
    ) -> None:
        with pytest.raises(ValidationError):
            SolanaAssetPolicy(
                option_id="sol_native",
                cluster="mainnet-beta",
                genesis_hash=MAINNET_GENESIS_HASH,
                kind=SolanaAssetKind.NATIVE,
                decimals=9,
                display_name="Solana",
                symbol="SOL",
                minimum_raw_amount="0",
            )
        with pytest.raises(ValidationError):
            SettlementSnapshot.model_validate(
                {
                    **native_settlement.model_dump(mode="json"),
                    "display_amount": "25",
                },
            )

    def test_settlement_policy_is_finalized_only(self, native_settlement) -> None:
        """Keeps confirmed as an intermediate RPC state, never a settlement policy."""
        with pytest.raises(ValidationError):
            SolanaAssetPolicy(
                option_id="sol_native",
                cluster="mainnet-beta",
                genesis_hash=MAINNET_GENESIS_HASH,
                kind=SolanaAssetKind.NATIVE,
                decimals=9,
                display_name="Solana",
                symbol="SOL",
                required_commitment="confirmed",
            )
        with pytest.raises(ValidationError):
            SettlementSnapshot.model_validate(
                {
                    **native_settlement.model_dump(mode="json"),
                    "required_commitment": "confirmed",
                },
            )
        with pytest.raises(ValidationError):
            SolanaPaymentOptionDTO(
                id="sol_native",
                label="Solana",
                symbol="SOL",
                decimals=9,
                minimum_raw_amount="1",
                maximum_raw_amount="100",
                required_commitment="confirmed",
            )

    def test_recipient_policy_provenance_is_required_and_canonical(self, native_settlement) -> None:
        payload = native_settlement.model_dump(mode="json")
        payload.pop("recipient_policy_version")
        with pytest.raises(ValidationError):
            SettlementSnapshot.model_validate(payload)
        with pytest.raises(ValidationError):
            SettlementSnapshot.model_validate(
                {
                    **native_settlement.model_dump(mode="json"),
                    "recipient_policy_version": " treasury policy v1 ",
                },
            )
