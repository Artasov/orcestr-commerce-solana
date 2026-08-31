from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orcestr_commerce_solana.clock import utc_datetime
from orcestr_commerce_solana.errors import SolanaRpcResponseError, SolanaRpcUnavailableError
from orcestr_commerce_solana.rpc.protocol import SolanaRpc
from orcestr_commerce_solana.schemas.assets import SettlementSnapshot, SolanaCommitment
from orcestr_commerce_solana.schemas.intents import SolanaIntentState
from orcestr_commerce_solana.schemas.transactions import TransactionIssuanceSnapshot
from orcestr_commerce_solana.services.verification.contracts import (
    VerificationDisposition,
    VerificationRequest,
    VerificationResult,
)
from orcestr_commerce_solana.services.verification.verifier import SolanaTransactionVerifier


class ReconciliationIntent(BaseModel):
    """Contains only persisted facts needed for one bounded reconciliation pass."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    public_id: UUID
    state: SolanaIntentState = SolanaIntentState.WAITING
    settlement: SettlementSnapshot
    issuances: tuple[TransactionIssuanceSnapshot, ...]
    candidate_signatures: tuple[str, ...] = ()
    verified_signature: str | None = None
    recorded_signatures: tuple[str, ...] = ()
    expires_at: datetime
    reconcile_until: datetime

    @field_validator("expires_at", "reconcile_until")
    @classmethod
    def validate_expiry(cls, value: datetime) -> datetime:
        return utc_datetime(value, "Reconciliation expiry")


class ReconciliationStats(BaseModel):
    """Reports deterministic batch counters for scheduler metrics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intents: int = Field(ge=0)
    candidates: int = Field(ge=0)
    applied: int = Field(ge=0)
    retryable: int = Field(ge=0)
    expired: int = Field(default=0, ge=0)


class ReconciliationCandidate(BaseModel):
    """Distinguishes an untrusted client hint from reference-indexed evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signature: str
    detection_source: str = Field(pattern=r"^(candidate|reference_scan)$")
    reference_indexed: bool


class ReferenceSignatureScan(BaseModel):
    """Returns bounded history candidates and whether the lower bound was reached."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[ReconciliationCandidate, ...]
    complete: bool
    reason_code: str | None = None


class ReconciliationStore(Protocol):
    """Lets a host apply row locks and CommerceXL runtime transitions atomically."""

    async def list_pending(self, *, limit: int, now: datetime) -> tuple[ReconciliationIntent, ...]:
        """Returns one bounded batch ordered by next-check time."""
        ...

    async def apply(self, intent_public_id: UUID, result: VerificationResult) -> None:
        """Persists transfer, audit event, and provider/core state in one transaction."""
        ...

    async def mark_retry(self, intent_public_id: UUID, reason_code: str | None) -> None:
        """Updates backoff without turning infrastructure uncertainty into failure."""
        ...

    async def expire(self, intent_public_id: UUID) -> None:
        """Atomically expires an unmatched intent after a successful final history scan."""
        ...

    async def apply_terminal_evidence(self, intent_public_id: UUID, result: VerificationResult) -> bool:
        """Persists one new finalized late transfer without reactivating a terminal payment."""
        ...

    async def finish_terminal(self, intent_public_id: UUID) -> None:
        """Stops post-terminal scans after the immutable horizon and a complete pass."""
        ...

    async def record_attempt(self, intent_public_id: UUID, result: VerificationResult) -> None:
        """Persists one deduplicated per-issuance audit without changing payment state."""
        ...

    async def record_duplicate_payment(self, intent_public_id: UUID, result: VerificationResult) -> bool:
        """Persists an additional finalized payment without repeating the product effect."""
        ...

    async def finish_paid_audit(self, intent_public_id: UUID) -> None:
        """Stops bounded duplicate-payment scans after the immutable horizon."""
        ...


class SolanaReconciler:
    """Performs a bounded sweep; it does not depend on Taskiq or run a watcher."""

    def __init__(
        self,
        rpc: SolanaRpc,
        verifier: SolanaTransactionVerifier,
        store: ReconciliationStore,
        *,
        signature_page_size: int = 100,
        max_signature_pages: int = 10,
    ) -> None:
        if signature_page_size < 1 or signature_page_size > 1000:
            raise ValueError("Signature page size must be between 1 and 1000.")
        if max_signature_pages < 1 or max_signature_pages > 100:
            raise ValueError("Maximum signature pages must be between 1 and 100.")
        self.rpc = rpc
        self.verifier = verifier
        self.store = store
        self.signature_page_size = signature_page_size
        self.max_signature_pages = max_signature_pages

    async def reconcile_pending(self, *, limit: int, now: datetime) -> ReconciliationStats:
        """Checks candidate hints and reference history for each pending intent once."""
        intents = await self.store.list_pending(limit=limit, now=now)
        candidate_count = 0
        applied_count = 0
        retryable_count = 0
        expired_count = 0
        for intent in intents:
            scan = await self._signatures(intent)
            candidate_count += len(scan.candidates)
            candidates = scan.candidates
            if intent.state in {
                SolanaIntentState.PAID,
                SolanaIntentState.CANCELLED,
                SolanaIntentState.EXPIRED,
            }:
                # Candidate signatures are unauthenticated acceleration hints. A
                # terminal audit waits for reference-indexed chain evidence.
                candidates = tuple(candidate for candidate in candidates if candidate.reference_indexed)
            if intent.state == SolanaIntentState.PAID:
                matches, attempts = await self._verify_paid_candidates(intent, candidates, now)
                for attempt in attempts:
                    await self.store.record_attempt(intent.public_id, attempt)
                for match in matches:
                    if await self.store.record_duplicate_payment(intent.public_id, match):
                        applied_count += 1
                if scan.complete and intent.reconcile_until <= now:
                    await self.store.finish_paid_audit(intent.public_id)
                    continue
                await self.store.mark_retry(intent.public_id, scan.reason_code)
                retryable_count += 1
                continue
            if intent.state in {SolanaIntentState.CANCELLED, SolanaIntentState.EXPIRED}:
                evidences, attempts, provisional_reason = await self._verify_terminal_candidates(
                    intent,
                    candidates,
                    now,
                )
                for attempt in attempts:
                    await self.store.record_attempt(intent.public_id, attempt)
                for evidence in evidences:
                    if await self.store.apply_terminal_evidence(intent.public_id, evidence):
                        applied_count += 1
                if scan.complete and intent.reconcile_until <= now:
                    await self.store.finish_terminal(intent.public_id)
                    continue
                await self.store.mark_retry(
                    intent.public_id,
                    provisional_reason or scan.reason_code,
                )
                retryable_count += 1
                continue

            if intent.expires_at <= now:
                # Public expiry is not extended by the evidence grace. A
                # complete pass first makes the payment terminal; any exact
                # finalized transfer is then recorded without product effect.
                terminal_candidates = tuple(
                    candidate for candidate in candidates if candidate.reference_indexed
                )
                evidences, attempts, provisional_reason = await self._verify_terminal_candidates(
                    intent,
                    terminal_candidates,
                    now,
                )
                if not scan.complete:
                    for attempt in attempts:
                        await self.store.record_attempt(intent.public_id, attempt)
                    await self.store.mark_retry(
                        intent.public_id,
                        provisional_reason or scan.reason_code,
                    )
                    retryable_count += 1
                    continue
                for attempt in attempts:
                    await self.store.record_attempt(intent.public_id, attempt)
                await self.store.expire(intent.public_id)
                expired_count += 1
                for evidence in evidences:
                    if await self.store.apply_terminal_evidence(intent.public_id, evidence):
                        applied_count += 1
                if intent.reconcile_until <= now:
                    await self.store.finish_terminal(intent.public_id)
                continue

            result, attempts = await self._verify_candidates(intent, candidates, now)
            for attempt in attempts:
                await self.store.record_attempt(intent.public_id, attempt)
            if result is None:
                await self.store.mark_retry(intent.public_id, scan.reason_code)
                retryable_count += 1
                continue
            if result.disposition == VerificationDisposition.UNKNOWN:
                await self.store.mark_retry(intent.public_id, result.reason_code)
                retryable_count += 1
                continue
            if result.disposition in {VerificationDisposition.OBSERVED, VerificationDisposition.CONFIRMED}:
                await self.store.apply(intent.public_id, result)
                await self.store.mark_retry(intent.public_id, result.reason_code)
                applied_count += 1
                retryable_count += 1
                continue
            await self.store.apply(intent.public_id, result)
            applied_count += 1
        return ReconciliationStats(
            intents=len(intents),
            candidates=candidate_count,
            applied=applied_count,
            retryable=retryable_count,
            expired=expired_count,
        )

    async def _signatures(self, intent: ReconciliationIntent) -> ReferenceSignatureScan:
        """Paginates reference history until the oldest issuance-time lower bound."""
        candidates: dict[str, ReconciliationCandidate] = {
            signature: ReconciliationCandidate(
                signature=signature,
                detection_source="candidate",
                reference_indexed=False,
            )
            for signature in intent.candidate_signatures
        }
        if not intent.issuances:
            return ReferenceSignatureScan(candidates=tuple(candidates.values()), complete=True)
        lower_slot = min(issuance.issued_context_slot for issuance in intent.issuances)
        before: str | None = None
        try:
            for _ in range(self.max_signature_pages):
                history = await self.rpc.get_signatures_for_address(
                    intent.settlement.reference,
                    before=before,
                    limit=self.signature_page_size,
                    commitment=SolanaCommitment.CONFIRMED,
                )
                if not history:
                    return ReferenceSignatureScan(candidates=tuple(candidates.values()), complete=True)
                reached_lower_bound = False
                for item in history:
                    if item.slot < lower_slot:
                        reached_lower_bound = True
                        break
                    candidates[item.signature] = ReconciliationCandidate(
                        signature=item.signature,
                        detection_source="reference_scan",
                        reference_indexed=True,
                    )
                if reached_lower_bound or len(history) < self.signature_page_size:
                    return ReferenceSignatureScan(candidates=tuple(candidates.values()), complete=True)
                next_before = history[-1].signature
                if next_before == before:
                    break
                before = next_before
        except (SolanaRpcUnavailableError, SolanaRpcResponseError) as exc:
            return ReferenceSignatureScan(
                candidates=tuple(candidates.values()),
                complete=False,
                reason_code=exc.code.value,
            )
        return ReferenceSignatureScan(
            candidates=tuple(candidates.values()),
            complete=False,
            reason_code="reference_scan_limit_reached",
        )

    async def _verify_candidates(
        self,
        intent: ReconciliationIntent,
        signatures: tuple[ReconciliationCandidate, ...],
        now: datetime,
    ) -> tuple[VerificationResult | None, tuple[VerificationResult, ...]]:
        """Searches all hints and prefers valid evidence over unrelated transactions."""
        if not intent.issuances:
            return None, ()
        confirmed: VerificationResult | None = None
        observed: VerificationResult | None = None
        unknown: VerificationResult | None = None
        attempts: list[VerificationResult] = []
        for candidate in signatures:
            prepared = await self.verifier.prepare(candidate.signature)
            for issuance in intent.issuances:
                result = await self.verifier.verify(
                    VerificationRequest(
                        signature=candidate.signature,
                        intent_public_id=intent.public_id,
                        settlement=intent.settlement,
                        issuance=issuance,
                        now=now,
                        detection_source=candidate.detection_source,
                    ),
                    prepared=prepared,
                )
                if result.disposition == VerificationDisposition.MATCH:
                    return result, tuple(attempts)
                if result.disposition == VerificationDisposition.CONFIRMED:
                    confirmed = confirmed or result
                    break
                if result.disposition == VerificationDisposition.OBSERVED:
                    if not candidate.reference_indexed:
                        break
                    observed = observed or result
                    break
                if result.disposition == VerificationDisposition.UNKNOWN:
                    if not candidate.reference_indexed:
                        break
                    unknown = unknown or result
                    break
                if result.reason_code == "issuance_mismatch":
                    continue
                # A failed or suspicious transaction belongs to one issuance,
                # not the whole payment intent. Keep searching current and
                # future issuances instead of making the intent terminal.
                if result.transfer is not None or result.attempt is not None:
                    attempts.append(result)
                break
        return confirmed or observed or unknown, tuple(attempts)

    async def _verify_paid_candidates(
        self,
        intent: ReconciliationIntent,
        signatures: tuple[ReconciliationCandidate, ...],
        now: datetime,
    ) -> tuple[tuple[VerificationResult, ...], tuple[VerificationResult, ...]]:
        """Collects every additional exact finalized payment in one bounded pass."""
        if not intent.issuances:
            return (), ()
        matches: list[VerificationResult] = []
        attempts: list[VerificationResult] = []
        for candidate in signatures:
            if candidate.signature == intent.verified_signature:
                continue
            prepared = await self.verifier.prepare(candidate.signature)
            for issuance in intent.issuances:
                result = await self.verifier.verify(
                    VerificationRequest(
                        signature=candidate.signature,
                        intent_public_id=intent.public_id,
                        settlement=intent.settlement,
                        issuance=issuance,
                        now=now,
                        detection_source=candidate.detection_source,
                    ),
                    prepared=prepared,
                )
                if result.reason_code == "issuance_mismatch":
                    continue
                if result.disposition == VerificationDisposition.MATCH:
                    matches.append(result)
                elif (
                    result.disposition in {VerificationDisposition.REVIEW, VerificationDisposition.FAILED}
                    and (result.transfer is not None or result.attempt is not None)
                ):
                    attempts.append(result)
                break
        return tuple(matches), tuple(attempts)

    async def _verify_terminal_candidates(
        self,
        intent: ReconciliationIntent,
        signatures: tuple[ReconciliationCandidate, ...],
        now: datetime,
    ) -> tuple[tuple[VerificationResult, ...], tuple[VerificationResult, ...], str | None]:
        """Collects every new finalized terminal transfer in one bounded pass."""
        if not intent.issuances:
            return (), (), None
        recorded = set(intent.recorded_signatures)
        if intent.verified_signature is not None:
            recorded.add(intent.verified_signature)
        evidences: list[VerificationResult] = []
        attempts: list[VerificationResult] = []
        provisional_reason: str | None = None
        for candidate in signatures:
            if not candidate.reference_indexed or candidate.signature in recorded:
                continue
            prepared = await self.verifier.prepare(candidate.signature)
            for issuance in intent.issuances:
                result = await self.verifier.verify(
                    VerificationRequest(
                        signature=candidate.signature,
                        intent_public_id=intent.public_id,
                        settlement=intent.settlement,
                        issuance=issuance,
                        now=now,
                        detection_source=candidate.detection_source,
                    ),
                    prepared=prepared,
                )
                if result.reason_code == "issuance_mismatch":
                    continue
                if self._is_terminal_evidence(result):
                    evidences.append(result)
                elif (
                    result.disposition in {VerificationDisposition.REVIEW, VerificationDisposition.FAILED}
                    and (result.transfer is not None or result.attempt is not None)
                ):
                    attempts.append(result)
                elif result.disposition in {
                    VerificationDisposition.UNKNOWN,
                    VerificationDisposition.OBSERVED,
                    VerificationDisposition.CONFIRMED,
                }:
                    provisional_reason = provisional_reason or result.reason_code
                break
        return tuple(evidences), tuple(attempts), provisional_reason

    @staticmethod
    def _is_terminal_evidence(result: VerificationResult) -> bool:
        """Accepts only exact finalized matches or the two explicit time-policy reviews."""
        if result.disposition == VerificationDisposition.MATCH:
            return result.transfer is not None and result.transfer.commitment == SolanaCommitment.FINALIZED
        return (
            result.disposition == VerificationDisposition.REVIEW
            and result.reason_code in {"late_payment", "block_time_unavailable"}
            and result.transfer is not None
            and result.transfer.commitment == SolanaCommitment.FINALIZED
        )
