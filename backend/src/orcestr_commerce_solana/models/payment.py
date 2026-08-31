from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from commercexl import CommerceBase
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class SolanaPaymentIntentORM(CommerceBase):
    """Persists one immutable Solana settlement snapshot per CommerceXL attempt."""

    __tablename__ = "commerce_solana_payment_intent"
    __table_args__ = (
        CheckConstraint("expected_raw_amount > 0", name="commerce_solana_intent_expected_amount_positive"),
        CheckConstraint(
            "expected_raw_amount <= 18446744073709551615",
            name="commerce_solana_intent_expected_amount_u64",
        ),
        UniqueConstraint(
            "cancel_actor_key",
            "cancel_idempotency_key",
            name="uq_commerce_solana_intent_cancel_actor_key",
        ),
        CheckConstraint(
            "reconcile_until > expires_at",
            name="commerce_solana_intent_reconcile_after_expiry",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(nullable=False, unique=True, index=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("commerce_payment.id"), nullable=False, unique=True, index=True)
    order_public_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    cluster: Mapped[str] = mapped_column(String(32), nullable=False)
    genesis_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_option_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    asset_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    mint: Mapped[str | None] = mapped_column(String(44), index=True)
    token_program: Mapped[str | None] = mapped_column(String(44))
    decimals: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_wallet: Mapped[str] = mapped_column(String(44), nullable=False, index=True)
    recipient_token_account: Mapped[str | None] = mapped_column(String(44), index=True)
    expected_raw_amount: Mapped[Decimal] = mapped_column(Numeric(20, 0), nullable=False)
    display_amount: Mapped[str] = mapped_column(String(128), nullable=False)
    reference: Mapped[str] = mapped_column(String(44), nullable=False, unique=True, index=True)
    quote_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    settlement_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    required_commitment: Mapped[str] = mapped_column(String(16), nullable=False)
    memo: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason_code: Mapped[str | None] = mapped_column(String(100))
    candidate_signature: Mapped[str | None] = mapped_column(String(100), index=True)
    verified_signature: Mapped[str | None] = mapped_column(String(100), index=True)
    cancel_idempotency_key: Mapped[str | None] = mapped_column(String(200))
    cancel_actor_key: Mapped[str | None] = mapped_column(String(100))
    cancel_reason: Mapped[str | None] = mapped_column(String(200))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    reconcile_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SolanaIntentCapabilityORM(CommerceBase):
    """Stores only a digest of one short-lived transaction-request capability."""

    __tablename__ = "commerce_solana_intent_capability"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    intent_id: Mapped[int] = mapped_column(
        ForeignKey("commerce_solana_payment_intent.id"),
        nullable=False,
        index=True,
    )
    secret_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SolanaTransactionIssuanceORM(CommerceBase):
    """Pins every unsigned transaction issued for an intent and payer."""

    __tablename__ = "commerce_solana_transaction_issuance"
    __table_args__ = (
        UniqueConstraint("intent_id", "message_sha256", name="uq_commerce_solana_issuance_intent_message"),
        CheckConstraint(
            "issued_context_slot >= 0",
            name="commerce_solana_issuance_context_slot_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    intent_id: Mapped[int] = mapped_column(
        ForeignKey("commerce_solana_payment_intent.id"),
        nullable=False,
        index=True,
    )
    capability_id: Mapped[int] = mapped_column(
        ForeignKey("commerce_solana_intent_capability.id"),
        nullable=False,
        index=True,
    )
    payer: Mapped[str] = mapped_column(String(44), nullable=False, index=True)
    recent_blockhash: Mapped[str] = mapped_column(String(44), nullable=False)
    last_valid_block_height: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issued_context_slot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    transaction_version: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepts_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SolanaTransferORM(CommerceBase):
    """Persists an observed on-chain transfer even when policy sends it to review."""

    __tablename__ = "commerce_solana_transfer"
    __table_args__ = (
        UniqueConstraint("cluster", "signature", name="uq_commerce_solana_transfer_cluster_signature"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    intent_id: Mapped[int] = mapped_column(
        ForeignKey("commerce_solana_payment_intent.id"),
        nullable=False,
        index=True,
    )
    issuance_id: Mapped[int | None] = mapped_column(ForeignKey("commerce_solana_transaction_issuance.id"), index=True)
    cluster: Mapped[str] = mapped_column(String(32), nullable=False)
    signature: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    instruction_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_account: Mapped[str] = mapped_column(String(44), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(44), nullable=False)
    mint: Mapped[str | None] = mapped_column(String(44), index=True)
    token_program: Mapped[str | None] = mapped_column(String(44))
    gross_raw_amount: Mapped[Decimal] = mapped_column(Numeric(20, 0), nullable=False)
    net_raw_amount: Mapped[Decimal] = mapped_column(Numeric(20, 0), nullable=False)
    slot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commitment: Mapped[str] = mapped_column(String(16), nullable=False)
    transaction_version: Mapped[str] = mapped_column(String(16), nullable=False)
    detection_source: Mapped[str] = mapped_column(String(32), nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(100))
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SolanaPaymentEventORM(CommerceBase):
    """Keeps an append-only provider audit trail without full RPC payloads."""

    __tablename__ = "commerce_solana_payment_event"
    __table_args__ = (
        UniqueConstraint("intent_id", "revision", name="uq_commerce_solana_event_intent_revision"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    intent_id: Mapped[int] = mapped_column(
        ForeignKey("commerce_solana_payment_intent.id"),
        nullable=False,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    previous_state: Mapped[str | None] = mapped_column(String(32))
    next_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(100))
    evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    actor_key: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
