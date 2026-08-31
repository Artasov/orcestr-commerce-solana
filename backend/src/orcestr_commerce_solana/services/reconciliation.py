from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orcestr_commerce_solana.clock import utc_datetime
from orcestr_commerce_solana.errors import (
    SolanaErrorCode,
    SolanaRpcResponseError,
    SolanaRpcUnavailableError,
)
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
    quarantined: int = Field(default=0, ge=0)


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
    overflow: bool = False
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

    async def quarantine(self, intent_public_id: UUID, reason_code: str) -> None:
        """Parks an anomalous reference history without guessing a financial outcome."""
        ...


class SolanaReconciler:
    """Performs a bounded sweep; it does not depend on Taskiq or run a watcher."""

    def __init__(
        self,
        rpc: SolanaRpc,
        verifier: SolanaTransactionVerifier,
        store: ReconciliationStore,
        *,
        max_candidate_verifications_per_intent: int = 16,
        max_candidate_verifications_per_pass: int = 32,
    ) -> None:
        if (
            max_candidate_verifications_per_intent < 1
            or max_candidate_verifications_per_intent > 64
        ):
            raise ValueError("Per-intent candidate verification budget must be between 1 and 64.")
        if (
            max_candidate_verifications_per_pass < max_candidate_verifications_per_intent
            or max_candidate_verifications_per_pass > 256
        ):
            raise ValueError(
                "Global candidate verification budget must cover one full intent and be at most 256."
            )
        self.rpc = rpc
        self.verifier = verifier
        self.store = store
        self.max_candidate_verifications_per_intent = max_candidate_verifications_per_intent
        self.max_candidate_verifications_per_pass = max_candidate_verifications_per_pass

    async def reconcile_pending(self, *, limit: int, now: datetime) -> ReconciliationStats:
        """Checks candidate hints and reference history for each pending intent once."""
        intents = await self.store.list_pending(limit=limit, now=now)
        candidate_count = 0
        applied_count = 0
        retryable_count = 0
        expired_count = 0
        quarantined_count = 0
        remaining_global_budget = self.max_candidate_verifications_per_pass
        for intent in intents:
            if remaining_global_budget < self.max_candidate_verifications_per_intent:
                await self.store.mark_retry(
                    intent.public_id,
                    SolanaErrorCode.CANDIDATE_VERIFICATION_GLOBAL_BUDGET_EXHAUSTED.value,
                )
                retryable_count += 1
                continue
            scan = await self._signatures(intent)
            candidate_count += len(scan.candidates)
            candidates = scan.candidates
            if intent.state in {
                SolanaIntentState.PAID,
                SolanaIntentState.CANCELLED,
                SolanaIntentState.EXPIRED,
            }:
                candidates = self._unrecorded_reference_candidates(intent, candidates)
            if intent.state == SolanaIntentState.PAID:
                bounded, verification_complete = self._bounded_candidates(candidates)
                matches, attempts, provisional, prepared_count = await self._verify_paid_candidates(
                    intent,
                    bounded,
                    now,
                )
                remaining_global_budget -= prepared_count
                for attempt in attempts:
                    await self.store.record_attempt(intent.public_id, attempt)
                for match in matches:
                    if await self.store.record_duplicate_payment(intent.public_id, match):
                        applied_count += 1
                if self._is_unknown(provisional):
                    await self.store.mark_retry(intent.public_id, provisional.reason_code)
                    retryable_count += 1
                    continue
                if scan.overflow or not verification_complete:
                    await self.store.quarantine(
                        intent.public_id,
                        SolanaErrorCode.REFERENCE_CANDIDATE_BUDGET_EXCEEDED.value,
                    )
                    quarantined_count += 1
                    continue
                if scan.complete and intent.reconcile_until <= now:
                    await self.store.finish_paid_audit(intent.public_id)
                    continue
                await self.store.mark_retry(
                    intent.public_id,
                    (provisional.reason_code if provisional is not None else None)
                    or scan.reason_code,
                )
                retryable_count += 1
                continue
            if intent.state in {SolanaIntentState.CANCELLED, SolanaIntentState.EXPIRED}:
                bounded, verification_complete = self._bounded_candidates(candidates)
                evidences, attempts, provisional, prepared_count = await self._verify_terminal_candidates(
                    intent,
                    bounded,
                    now,
                )
                remaining_global_budget -= prepared_count
                for attempt in attempts:
                    await self.store.record_attempt(intent.public_id, attempt)
                for evidence in evidences:
                    if await self.store.apply_terminal_evidence(intent.public_id, evidence):
                        applied_count += 1
                if self._is_unknown(provisional):
                    await self.store.mark_retry(intent.public_id, provisional.reason_code)
                    retryable_count += 1
                    continue
                if scan.overflow or not verification_complete:
                    await self.store.quarantine(
                        intent.public_id,
                        SolanaErrorCode.REFERENCE_CANDIDATE_BUDGET_EXCEEDED.value,
                    )
                    quarantined_count += 1
                    continue
                if scan.complete and intent.reconcile_until <= now:
                    await self.store.finish_terminal(intent.public_id)
                    continue
                await self.store.mark_retry(
                    intent.public_id,
                    (provisional.reason_code if provisional is not None else None)
                    or scan.reason_code,
                )
                retryable_count += 1
                continue

            if intent.expires_at <= now:
                # Public expiry stops new actions, but it must not erase an
                # exact transfer submitted inside an issuance acceptance
                # window. Reference-indexed finalized evidence can therefore
                # settle before expiry is persisted. Exact confirmed evidence
                # remains pending only through the immutable grace horizon.
                terminal_candidates = self._unrecorded_reference_candidates(
                    intent,
                    candidates,
                )
                bounded, verification_complete = self._bounded_candidates(terminal_candidates)
                evidences, attempts, provisional, prepared_count = await self._verify_terminal_candidates(
                    intent,
                    bounded,
                    now,
                )
                remaining_global_budget -= prepared_count
                matches = tuple(
                    evidence
                    for evidence in evidences
                    if evidence.disposition == VerificationDisposition.MATCH
                )
                policy_reviews = tuple(
                    evidence
                    for evidence in evidences
                    if evidence.disposition == VerificationDisposition.REVIEW
                )
                if matches and intent.reconcile_until > now:
                    for attempt in (*attempts, *policy_reviews):
                        await self.store.record_attempt(intent.public_id, attempt)
                    await self.store.apply(intent.public_id, matches[0])
                    applied_count += 1
                    for duplicate in matches[1:]:
                        if await self.store.record_duplicate_payment(
                            intent.public_id,
                            duplicate,
                        ):
                            applied_count += 1
                    continue
                if self._is_unknown(provisional):
                    for attempt in attempts:
                        await self.store.record_attempt(intent.public_id, attempt)
                    await self.store.mark_retry(intent.public_id, provisional.reason_code)
                    retryable_count += 1
                    continue
                if (
                    provisional is not None
                    and provisional.disposition == VerificationDisposition.CONFIRMED
                    and intent.reconcile_until > now
                ):
                    for attempt in (*attempts, *policy_reviews):
                        await self.store.record_attempt(intent.public_id, attempt)
                    await self.store.apply(intent.public_id, provisional)
                    applied_count += 1
                    if scan.overflow or not verification_complete:
                        await self.store.quarantine(
                            intent.public_id,
                            SolanaErrorCode.REFERENCE_CANDIDATE_BUDGET_EXCEEDED.value,
                        )
                        quarantined_count += 1
                    else:
                        await self.store.mark_retry(intent.public_id, provisional.reason_code)
                        retryable_count += 1
                    continue
                if scan.overflow or not verification_complete:
                    for attempt in attempts:
                        await self.store.record_attempt(intent.public_id, attempt)
                    await self.store.quarantine(
                        intent.public_id,
                        SolanaErrorCode.REFERENCE_CANDIDATE_BUDGET_EXCEEDED.value,
                    )
                    quarantined_count += 1
                    continue
                if not scan.complete:
                    for attempt in attempts:
                        await self.store.record_attempt(intent.public_id, attempt)
                    await self.store.mark_retry(
                        intent.public_id,
                        (provisional.reason_code if provisional is not None else None)
                        or scan.reason_code,
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

            bounded, verification_complete = self._bounded_candidates(candidates)
            result, attempts, prepared_count = await self._verify_candidates(
                intent,
                bounded,
                now,
            )
            remaining_global_budget -= prepared_count
            for attempt in attempts:
                await self.store.record_attempt(intent.public_id, attempt)
            if self._is_unknown(result):
                await self.store.mark_retry(intent.public_id, result.reason_code)
                retryable_count += 1
                continue
            if result is not None and result.disposition in {
                VerificationDisposition.OBSERVED,
                VerificationDisposition.CONFIRMED,
            }:
                await self.store.apply(intent.public_id, result)
                applied_count += 1
                if scan.overflow or not verification_complete:
                    await self.store.quarantine(
                        intent.public_id,
                        SolanaErrorCode.REFERENCE_CANDIDATE_BUDGET_EXCEEDED.value,
                    )
                    quarantined_count += 1
                else:
                    await self.store.mark_retry(intent.public_id, result.reason_code)
                    retryable_count += 1
                continue
            if result is not None:
                await self.store.apply(intent.public_id, result)
                applied_count += 1
                continue
            if scan.overflow or not verification_complete:
                await self.store.quarantine(
                    intent.public_id,
                    SolanaErrorCode.REFERENCE_CANDIDATE_BUDGET_EXCEEDED.value,
                )
                quarantined_count += 1
                continue
            await self.store.mark_retry(intent.public_id, scan.reason_code)
            retryable_count += 1
        return ReconciliationStats(
            intents=len(intents),
            candidates=candidate_count,
            applied=applied_count,
            retryable=retryable_count,
            expired=expired_count,
            quarantined=quarantined_count,
        )

    def _bounded_candidates(
        self,
        candidates: tuple[ReconciliationCandidate, ...],
    ) -> tuple[tuple[ReconciliationCandidate, ...], bool]:
        """Returns at most one per-intent prepare budget and an exact completeness flag."""

        limit = self.max_candidate_verifications_per_intent
        return candidates[:limit], len(candidates) <= limit

    @staticmethod
    def _unrecorded_reference_candidates(
        intent: ReconciliationIntent,
        candidates: tuple[ReconciliationCandidate, ...],
    ) -> tuple[ReconciliationCandidate, ...]:
        """Excludes client-only hints and already persisted transfer signatures for audits."""

        recorded = set(intent.recorded_signatures)
        if intent.verified_signature is not None:
            recorded.add(intent.verified_signature)
        return tuple(
            candidate
            for candidate in candidates
            if candidate.reference_indexed and candidate.signature not in recorded
        )

    @staticmethod
    def _is_unknown(result: VerificationResult | None) -> bool:
        return (
            result is not None
            and result.disposition == VerificationDisposition.UNKNOWN
        )

    async def _signatures(self, intent: ReconciliationIntent) -> ReferenceSignatureScan:
        """Reads one budget-plus-one window; untrusted history is never paginated."""
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
        scan_limit = self.max_candidate_verifications_per_intent + 1
        try:
            history = await self.rpc.get_signatures_for_address(
                intent.settlement.reference,
                before=None,
                limit=scan_limit,
                commitment=SolanaCommitment.CONFIRMED,
            )
        except (SolanaRpcUnavailableError, SolanaRpcResponseError) as exc:
            return ReferenceSignatureScan(
                candidates=tuple(candidates.values()),
                complete=False,
                reason_code=exc.code.value,
            )
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
        if reached_lower_bound or len(history) < scan_limit:
            return ReferenceSignatureScan(candidates=tuple(candidates.values()), complete=True)
        return ReferenceSignatureScan(
            candidates=tuple(candidates.values()),
            complete=False,
            overflow=True,
            reason_code=SolanaErrorCode.REFERENCE_CANDIDATE_BUDGET_EXCEEDED.value,
        )

    async def _verify_candidates(
        self,
        intent: ReconciliationIntent,
        signatures: tuple[ReconciliationCandidate, ...],
        now: datetime,
    ) -> tuple[VerificationResult | None, tuple[VerificationResult, ...], int]:
        """Searches all hints and prefers valid evidence over unrelated transactions."""
        if not intent.issuances:
            return None, (), 0
        confirmed: VerificationResult | None = None
        observed: VerificationResult | None = None
        unknown: VerificationResult | None = None
        attempts: list[VerificationResult] = []
        prepared_count = 0
        for candidate in signatures:
            prepared = await self.verifier.prepare(candidate.signature)
            prepared_count += 1
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
                    return result, tuple(attempts), prepared_count
                if result.disposition == VerificationDisposition.CONFIRMED:
                    confirmed = confirmed or result
                    break
                if result.disposition == VerificationDisposition.OBSERVED:
                    if not candidate.reference_indexed:
                        break
                    observed = observed or result
                    break
                if result.disposition == VerificationDisposition.UNKNOWN:
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
        return unknown or confirmed or observed, tuple(attempts), prepared_count

    async def _verify_paid_candidates(
        self,
        intent: ReconciliationIntent,
        signatures: tuple[ReconciliationCandidate, ...],
        now: datetime,
    ) -> tuple[
        tuple[VerificationResult, ...],
        tuple[VerificationResult, ...],
        VerificationResult | None,
        int,
    ]:
        """Collects every additional exact finalized payment in one bounded pass."""
        if not intent.issuances:
            return (), (), None, 0
        matches: list[VerificationResult] = []
        attempts: list[VerificationResult] = []
        provisional: VerificationResult | None = None
        prepared_count = 0
        for candidate in signatures:
            if candidate.signature == intent.verified_signature:
                continue
            prepared = await self.verifier.prepare(candidate.signature)
            prepared_count += 1
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
                elif result.disposition in {
                    VerificationDisposition.UNKNOWN,
                    VerificationDisposition.OBSERVED,
                    VerificationDisposition.CONFIRMED,
                }:
                    provisional = self._preferred_provisional(provisional, result)
                break
        return tuple(matches), tuple(attempts), provisional, prepared_count

    async def _verify_terminal_candidates(
        self,
        intent: ReconciliationIntent,
        signatures: tuple[ReconciliationCandidate, ...],
        now: datetime,
    ) -> tuple[
        tuple[VerificationResult, ...],
        tuple[VerificationResult, ...],
        VerificationResult | None,
        int,
    ]:
        """Collects every new finalized terminal transfer in one bounded pass."""
        if not intent.issuances:
            return (), (), None, 0
        recorded = set(intent.recorded_signatures)
        if intent.verified_signature is not None:
            recorded.add(intent.verified_signature)
        evidences: list[VerificationResult] = []
        attempts: list[VerificationResult] = []
        provisional: VerificationResult | None = None
        prepared_count = 0
        for candidate in signatures:
            if not candidate.reference_indexed or candidate.signature in recorded:
                continue
            prepared = await self.verifier.prepare(candidate.signature)
            prepared_count += 1
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
                    provisional = self._preferred_provisional(provisional, result)
                break
        return tuple(evidences), tuple(attempts), provisional, prepared_count

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

    @staticmethod
    def _preferred_provisional(
        current: VerificationResult | None,
        candidate: VerificationResult,
    ) -> VerificationResult:
        """Keeps UNKNOWN dominant so uncertainty cannot complete a lifecycle audit."""

        if current is None:
            return candidate
        rank = {
            VerificationDisposition.OBSERVED: 1,
            VerificationDisposition.CONFIRMED: 2,
            VerificationDisposition.UNKNOWN: 3,
        }
        return candidate if rank[candidate.disposition] > rank[current.disposition] else current
