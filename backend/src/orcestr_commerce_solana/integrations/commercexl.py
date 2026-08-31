from __future__ import annotations

from dataclasses import dataclass

from commercexl import (
    AbstractPaymentService,
    CheckoutAction,
    PaymentCreateContext,
    PaymentCreateResult,
    PaymentOption,
    PaymentProviderRegistration,
    PaymentState,
    PaymentVerificationResult,
)
from sqlalchemy.ext.asyncio import AsyncSession

from orcestr_commerce_solana.amounts import SolanaAmountCodec
from orcestr_commerce_solana.config import SolanaCommerceConfig
from orcestr_commerce_solana.repositories import IntentCreation, SolanaIntentRepository
from orcestr_commerce_solana.schemas.assets import SettlementQuoteSnapshot, SettlementSnapshot, SolanaAssetKind
from orcestr_commerce_solana.services.assets.registry import AssetRegistry
from orcestr_commerce_solana.services.intents.ports import (
    RecipientContext,
    RecipientResolver,
    SettlementQuoteProvider,
    SettlementQuoteRequest,
    SettlementPriceKeyResolver,
)
from orcestr_commerce_solana.services.verification.contracts import (
    VerificationDisposition,
    VerificationResult,
    VerifiedTransfer,
)


@dataclass(frozen=True)
class SolanaProviderDependencies:
    """Contains all host-owned services injected into the CommerceXL provider."""

    config: SolanaCommerceConfig
    assets: AssetRegistry
    recipients: RecipientResolver
    price_keys: SettlementPriceKeyResolver
    quotes: SettlementQuoteProvider
    intents: SolanaIntentRepository


class SolanaPaymentService(AbstractPaymentService):
    """Creates Solana child intents without executing or mutating orders directly."""

    def __init__(self, commerce: object, registration: PaymentProviderRegistration, dependencies: SolanaProviderDependencies) -> None:
        super().__init__(commerce, registration)
        self.dependencies = dependencies

    async def list_options(self, session: AsyncSession, order: object, actor: object) -> tuple[PaymentOption, ...]:
        """Publishes only active server-owned assets; clients cannot submit a mint."""
        price_key = await self.dependencies.price_keys.resolve(session, order, actor)
        if price_key is None:
            return ()
        self._validate_price_key(price_key)
        options: list[PaymentOption] = []
        for asset in await self.dependencies.assets.list_active():
            policy = asset.policy
            if not self._is_kind_enabled(policy.kind):
                continue
            recipient_context = RecipientContext(
                order_public_id=order.id,
                payer_actor_key=str(actor.id),
            )
            quote_request = SettlementQuoteRequest(
                order_public_id=order.id,
                price_key=price_key,
                commercial_amount=str(order.amount),
                commercial_currency=order.currency,
                asset=policy,
            )
            if not await self.dependencies.recipients.is_available(recipient_context, asset):
                continue
            if not await self.dependencies.quotes.is_available(quote_request):
                continue
            options.append(
                PaymentOption(
                    id=policy.option_id,
                    label=f"{policy.display_name} ({policy.symbol})",
                    action_kind="solana_transaction_request",
                    details={
                        "asset_kind": policy.kind.value,
                        "mint": policy.mint,
                        "symbol": policy.symbol,
                        "decimals": policy.decimals,
                        "required_commitment": policy.required_commitment.value,
                        "minimum_raw_amount": policy.minimum_raw_amount,
                        "maximum_raw_amount": policy.maximum_raw_amount,
                    },
                ),
            )
        return tuple(options)

    async def create(self, session: AsyncSession, context: PaymentCreateContext) -> PaymentCreateResult:
        """Resolves recipient and quote from server facts, then persists an immutable intent."""
        asset = await self.dependencies.assets.get(context.option.id)
        if asset is None:
            raise self.commerce.get_bad_request("Solana payment option is unavailable.")
        if not self._is_kind_enabled(asset.policy.kind):
            raise self.commerce.get_bad_request("Solana asset kind is disabled.")
        if context.public_base_url is not None and context.public_base_url.rstrip("/") != self.dependencies.config.public_base_url:
            raise self.commerce.get_bad_request("Solana public base URL does not match trusted configuration.")
        price_key = await self.dependencies.price_keys.resolve(session, context.order, context.actor)
        if price_key is None:
            raise self.commerce.get_bad_request("Solana pricing is unavailable for this product.")
        self._validate_price_key(price_key)
        recipient = await self.dependencies.recipients.resolve(
            RecipientContext(
                order_public_id=context.order.id,
                payer_actor_key=str(context.actor.id),
            ),
            asset,
        )
        quote = await self.dependencies.quotes.quote(
            SettlementQuoteRequest(
                order_public_id=context.order.id,
                price_key=price_key,
                commercial_amount=str(context.order.amount),
                commercial_currency=context.order.currency,
                asset=asset.policy,
            ),
        )
        raw_amount = int(quote.expected_raw_amount)
        if raw_amount < int(asset.policy.minimum_raw_amount) or raw_amount > int(asset.policy.maximum_raw_amount):
            raise self.commerce.get_bad_request("Solana settlement quote is outside configured limits.")
        if asset.policy.kind == SolanaAssetKind.TOKEN and recipient.token_account is None:
            raise self.commerce.get_bad_request("Token-2022 recipient account is not configured.")

        now = self.dependencies.intents.clock.now()
        expires_at = now + self.dependencies.config.intent_ttl
        settlement = SettlementSnapshot(
            cluster=asset.policy.cluster,
            genesis_hash=asset.policy.genesis_hash,
            asset_option_id=asset.policy.option_id,
            kind=asset.policy.kind,
            asset_name=asset.policy.display_name,
            asset_symbol=asset.policy.symbol,
            mint=asset.policy.mint,
            token_program=asset.policy.token_program,
            decimals=asset.policy.decimals,
            recipient_wallet=recipient.wallet,
            recipient_token_account=recipient.token_account,
            recipient_policy_version=recipient.policy_version,
            expected_raw_amount=quote.expected_raw_amount,
            display_amount=SolanaAmountCodec.format(raw_amount, asset.policy.decimals),
            reference=self.dependencies.intents.new_reference(),
            required_commitment=asset.policy.required_commitment,
            quote=SettlementQuoteSnapshot(
                source=quote.source,
                version=quote.version,
                commercial_amount=str(context.order.amount),
                commercial_currency=context.order.currency,
                rate_numerator=quote.rate_numerator,
                rate_denominator=quote.rate_denominator,
                rounding=quote.rounding,
            ),
            memo=f"payment:{context.payment.public_id}",
        )
        action = await self.dependencies.intents.create(
            session,
            IntentCreation(
                payment_id=context.payment.id,
                order_public_id=context.order.id,
                settlement=settlement,
                expires_at=expires_at,
            ),
        )
        return PaymentCreateResult(
            action=CheckoutAction(
                kind="solana_transaction_request",
                uri=action.uri,
                expires_at=action.expires_at,
                payload={
                    "intent_public_id": str(action.intent_public_id),
                    "asset_option_id": asset.policy.option_id,
                },
            ),
        )

    async def get_action(self, session: AsyncSession, payment: object) -> CheckoutAction:
        """Reissues a short-lived capability without persisting the raw action URI."""
        intent = await self.dependencies.intents.get_by_payment(session, payment.id)
        if intent is None:
            raise self.commerce.get_not_found("Solana payment intent not found.")
        if not self._is_kind_enabled(SolanaAssetKind(intent.asset_kind)):
            raise self.commerce.get_bad_request("Solana asset kind is disabled.")
        action = await self.dependencies.intents.issue_action(session, intent)
        return CheckoutAction(
            kind="solana_transaction_request",
            uri=action.uri,
            expires_at=action.expires_at,
            payload={"intent_public_id": str(action.intent_public_id)},
        )

    async def cancel(self, session: AsyncSession, payment: object) -> PaymentVerificationResult:
        """Revokes capabilities while reconciliation retains late-payment evidence."""
        intent = await self.dependencies.intents.get_by_payment(session, payment.id)
        if intent is None:
            raise self.commerce.get_not_found("Solana payment intent not found.")
        if intent.cancel_idempotency_key is None or intent.cancel_actor_key is None:
            raise self.commerce.get_conflict(
                "Solana payments must be cancelled through SolanaApplicationService.cancel_intent().",
            )
        await self.dependencies.intents.cancel(session, payment.id, "cancelled")
        return PaymentVerificationResult(
            state=PaymentState.CANCELLED,
            reason_code=intent.cancel_reason or "cancelled",
        )

    def _is_kind_enabled(self, kind: SolanaAssetKind) -> bool:
        """Applies host kill switches to option discovery, creation, and reissue."""
        if kind == SolanaAssetKind.NATIVE:
            return self.dependencies.config.enable_native_sol
        return self.dependencies.config.enable_token_2022

    def _validate_price_key(self, price_key: str) -> None:
        """Rejects ambiguous or unbounded host identities before quote lookup."""
        if not isinstance(price_key, str) or not price_key.strip() or len(price_key) > 100:
            raise self.commerce.get_bad_request("Solana product price key is invalid.")
        if price_key != price_key.strip():
            raise self.commerce.get_bad_request("Solana product price key must be canonical.")


class SolanaPaymentServiceFactory:
    """Creates provider instances while preserving explicit dependency injection."""

    def __init__(self, dependencies: SolanaProviderDependencies) -> None:
        self.dependencies = dependencies

    def __call__(self, commerce: object, registration: PaymentProviderRegistration) -> SolanaPaymentService:
        """Builds one service for the CommerceXL runtime registry."""
        return SolanaPaymentService(commerce, registration, self.dependencies)


class SolanaProviderRegistrationFactory:
    """Builds the strict CommerceXL registration used by host configuration."""

    @staticmethod
    def create(dependencies: SolanaProviderDependencies) -> PaymentProviderRegistration:
        """Registers one canonical Solana system and provider kind."""
        return PaymentProviderRegistration(
            system="solana",
            provider_kind="solana",
            factory=SolanaPaymentServiceFactory(dependencies),
        )


class CommerceVerificationMapper:
    """Maps verified Solana facts to core states without treating UNKNOWN as failure."""

    @staticmethod
    def map(result: VerificationResult) -> PaymentVerificationResult | None:
        """Returns None for retryable uncertainty so core state remains unchanged."""
        if result.disposition == VerificationDisposition.UNKNOWN:
            return None
        if result.disposition == VerificationDisposition.OBSERVED:
            # A processed signature is only a provisional signal: it has not
            # been decoded against the issued message yet. Use PROCESSING so
            # CommerceXL emits an update without claiming unique evidence.
            return PaymentVerificationResult(
                state=PaymentState.PROCESSING,
                reason_code=result.reason_code,
            )
        if result.disposition == VerificationDisposition.FAILED:
            return PaymentVerificationResult(state=PaymentState.FAILED, reason_code=result.reason_code)
        if result.disposition == VerificationDisposition.REVIEW:
            return PaymentVerificationResult(state=PaymentState.REVIEW, reason_code=result.reason_code)
        if result.transfer is None:
            raise ValueError("Verified Solana result must include normalized transfer evidence.")
        evidence = result.transfer.model_dump(mode="json")
        evidence_key = CommerceVerificationMapper.evidence_key(result.transfer)
        if result.disposition == VerificationDisposition.CONFIRMED:
            return PaymentVerificationResult(
                state=PaymentState.CONFIRMED,
                evidence_key=evidence_key,
                reason_code=result.reason_code,
                evidence=evidence,
            )
        return PaymentVerificationResult(
            state=PaymentState.PAID,
            evidence_key=evidence_key,
            evidence=evidence,
        )

    @staticmethod
    def evidence_key(transfer: VerifiedTransfer) -> str:
        """Scopes a transaction signature by cluster and instruction identity."""
        return f"{transfer.cluster}:{transfer.signature}:{transfer.instruction_index}"
