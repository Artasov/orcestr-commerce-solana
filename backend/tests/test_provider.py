from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from commercexl import PaymentOptionDTO, PaymentProviderRegistration

from orcestr_commerce_solana.clock import FrozenClock
from orcestr_commerce_solana.config import SolanaCommerceConfig
from orcestr_commerce_solana.constants import MAINNET_GENESIS_HASH, ORCESTR_TOKEN_MINT, TOKEN_2022_PROGRAM_ID
from orcestr_commerce_solana.integrations.commercexl import SolanaPaymentService, SolanaProviderDependencies
from orcestr_commerce_solana.repositories import IntentActionIssue, SolanaIntentRepository
from orcestr_commerce_solana.schemas.assets import (
    SolanaAssetKind,
    SolanaAssetPolicy,
    ValidatedSolanaAsset,
)
from orcestr_commerce_solana.services.assets import StaticAssetRegistry
from orcestr_commerce_solana.services.intents import (
    FixedSettlementQuoteProvider,
    OrderSnapshotSettlementQuoteProvider,
    RecipientSnapshot,
    StaticRecipientResolver,
)
from orcestr_commerce_solana.services.intents.ports import SettlementQuoteRequest
from solders.pubkey import Pubkey


class ProductCodePriceKeyResolver:
    """Models the host adapter that resolves a stable Beauty plan or pack code."""

    async def resolve(self, session, order, actor) -> str | None:
        _ = session
        _ = actor
        return getattr(order, "product_code", None)


class FakeCommerce:
    """Creates host-style exceptions for provider unit tests."""

    @staticmethod
    def get_bad_request(message: str) -> ValueError:
        return ValueError(message)

    @staticmethod
    def get_not_found(message: str) -> LookupError:
        return LookupError(message)

    @staticmethod
    def get_conflict(message: str) -> RuntimeError:
        return RuntimeError(message)


class TestSolanaPaymentServiceOptions:
    """Checks order-specific preflight and feature flag enforcement."""

    @pytest.mark.asyncio
    async def test_option_requires_recipient_and_quote_preflight(self, now) -> None:
        asset = self._token_asset()
        config = SolanaCommerceConfig(
            public_base_url="https://pay.example.com",
            merchant_label="Merchant",
        )
        empty_quotes = FixedSettlementQuoteProvider({})
        recipient = RecipientSnapshot(
            wallet="11111111111111111111111111111111",
            token_account="11111111111111111111111111111111",
            policy_version="1",
        )
        service = self._service(config, asset, {asset.policy.option_id: recipient}, empty_quotes, now)
        order = SimpleNamespace(
            id=uuid4(),
            amount="10.00",
            currency="USD",
            kind="beauty_tariff",
            product_code="beauty_tariff",
        )
        actor = SimpleNamespace(id=1)

        assert await service.list_options(SimpleNamespace(), order, actor) == ()

        service = self._service(
            config,
            asset,
            {asset.policy.option_id: recipient},
            FixedSettlementQuoteProvider({("beauty_tariff", asset.policy.option_id): "1000000"}),
            now,
        )
        options = await service.list_options(SimpleNamespace(), order, actor)
        assert [item.id for item in options] == [asset.policy.option_id]

    @pytest.mark.asyncio
    async def test_disabled_token_kind_is_not_published_or_created(self, now) -> None:
        asset = self._token_asset()
        config = SolanaCommerceConfig(
            public_base_url="https://pay.example.com",
            merchant_label="Merchant",
            enable_native_sol=True,
            enable_token_2022=False,
        )
        recipient = RecipientSnapshot(
            wallet="11111111111111111111111111111111",
            token_account="11111111111111111111111111111111",
            policy_version="1",
        )
        service = self._service(
            config,
            asset,
            {asset.policy.option_id: recipient},
            FixedSettlementQuoteProvider({("beauty_tariff", asset.policy.option_id): "1000000"}),
            now,
        )
        order = SimpleNamespace(
            id=uuid4(),
            amount="10.00",
            currency="USD",
            kind="beauty_tariff",
            product_code="beauty_tariff",
        )
        actor = SimpleNamespace(id=1)

        assert await service.list_options(SimpleNamespace(), order, actor) == ()
        context = SimpleNamespace(
            option=PaymentOptionDTO(
                id=asset.policy.option_id,
                label="Orcestr",
                action_kind="solana_transaction_request",
                amount=order.amount,
                currency=order.currency,
                payment_system="solana",
                provider_kind="solana",
            ),
            public_base_url=config.public_base_url,
            order=order,
            actor=actor,
            payment=SimpleNamespace(id=1, public_id=uuid4()),
        )
        with pytest.raises(ValueError, match="disabled"):
            await service.create(SimpleNamespace(), context)

    @pytest.mark.asyncio
    async def test_fixed_quote_is_scoped_by_product_and_asset(self) -> None:
        asset = self._token_asset()
        quotes = FixedSettlementQuoteProvider(
            {
                ("beauty_credits_100", asset.policy.option_id): "1000000",
                ("beauty_credits_500", asset.policy.option_id): "4500000",
            },
        )

        first = await quotes.quote(
            SettlementQuoteRequest(
                order_public_id=uuid4(),
                price_key="beauty_credits_100",
                commercial_amount="10",
                commercial_currency="USD",
                asset=asset.policy,
            ),
        )
        second = await quotes.quote(
            SettlementQuoteRequest(
                order_public_id=uuid4(),
                price_key="beauty_credits_500",
                commercial_amount="40",
                commercial_currency="USD",
                asset=asset.policy,
            ),
        )

        assert first.expected_raw_amount == "1000000"
        assert second.expected_raw_amount == "4500000"

    @pytest.mark.asyncio
    async def test_same_order_kind_uses_distinct_resolved_product_prices(self, now) -> None:
        class CapturingIntents:
            def __init__(self) -> None:
                self.clock = FrozenClock(now)
                self.creations = []

            @staticmethod
            def new_reference() -> str:
                return str(Pubkey.new_unique())

            async def create(self, session, creation):
                _ = session
                self.creations.append(creation)
                return IntentActionIssue(
                    intent_public_id=uuid4(),
                    uri="https://pay.example.com/commerce/solana/transaction-requests/test",
                    expires_at=creation.expires_at,
                )

        asset = self._token_asset()
        option_id = asset.policy.option_id
        config = SolanaCommerceConfig(public_base_url="https://pay.example.com", merchant_label="Merchant")
        recipient = RecipientSnapshot(
            wallet="11111111111111111111111111111111",
            token_account="11111111111111111111111111111111",
            policy_version="1",
        )
        intents = CapturingIntents()
        dependencies = SolanaProviderDependencies(
            config=config,
            assets=StaticAssetRegistry((asset,)),
            recipients=StaticRecipientResolver({option_id: recipient}),
            price_keys=ProductCodePriceKeyResolver(),
            quotes=FixedSettlementQuoteProvider(
                {
                    ("beauty.credits.100", option_id): "1000000",
                    ("beauty.credits.500", option_id): "4500000",
                },
            ),
            intents=intents,
        )
        registration = PaymentProviderRegistration(
            system="solana",
            provider_kind="solana",
            factory=lambda commerce, item: SolanaPaymentService(commerce, item, dependencies),
        )
        service = SolanaPaymentService(FakeCommerce(), registration, dependencies)
        actor = SimpleNamespace(id=1)
        orders = (
            SimpleNamespace(
                id=uuid4(),
                amount="10.00",
                currency="USD",
                kind="ai_credit_pack",
                product_code="beauty.credits.100",
            ),
            SimpleNamespace(
                id=uuid4(),
                amount="40.00",
                currency="USD",
                kind="ai_credit_pack",
                product_code="beauty.credits.500",
            ),
        )
        for index, order in enumerate(orders, start=1):
            await service.create(
                SimpleNamespace(),
                SimpleNamespace(
                    option=PaymentOptionDTO(
                        id=option_id,
                        label="Orcestr",
                        action_kind="solana_transaction_request",
                        amount=order.amount,
                        currency=order.currency,
                        payment_system="solana",
                        provider_kind="solana",
                    ),
                    public_base_url=config.public_base_url,
                    order=order,
                    actor=actor,
                    payment=SimpleNamespace(id=index, public_id=uuid4()),
                ),
            )

        assert [item.settlement.expected_raw_amount for item in intents.creations] == ["1000000", "4500000"]
        assert [item.settlement.recipient_policy_version for item in intents.creations] == ["1", "1"]
        assert orders[0].kind == orders[1].kind

    @pytest.mark.asyncio
    async def test_order_snapshot_quote_freezes_db_orcestr_price(self, now) -> None:
        class CapturingIntents:
            def __init__(self) -> None:
                self.clock = FrozenClock(now)
                self.creation = None

            @staticmethod
            def new_reference() -> str:
                return str(Pubkey.new_unique())

            async def create(self, session, creation):
                _ = session
                self.creation = creation
                return IntentActionIssue(
                    intent_public_id=uuid4(),
                    uri="https://pay.example.com/commerce/solana/transaction-requests/test",
                    expires_at=creation.expires_at,
                )

        asset = self._token_asset()
        option_id = asset.policy.option_id
        order = SimpleNamespace(
            id=uuid4(),
            amount=Decimal("125.123456"),
            currency="ORCESTR",
            kind="ai_credit_pack",
            product_code="beauty.credits.100",
        )
        recipient = RecipientSnapshot(
            wallet="11111111111111111111111111111111",
            token_account="11111111111111111111111111111111",
            policy_version="treasury-v1",
        )
        intents = CapturingIntents()
        dependencies = SolanaProviderDependencies(
            config=SolanaCommerceConfig(public_base_url="https://pay.example.com", merchant_label="Merchant"),
            assets=StaticAssetRegistry((asset,)),
            recipients=StaticRecipientResolver({option_id: recipient}),
            price_keys=ProductCodePriceKeyResolver(),
            quotes=OrderSnapshotSettlementQuoteProvider({option_id: "ORCESTR"}, version="catalog-v1"),
            intents=intents,
        )
        service = SolanaPaymentService(
            FakeCommerce(),
            PaymentProviderRegistration(
                system="solana",
                provider_kind="solana",
                factory=lambda commerce, item: SolanaPaymentService(commerce, item, dependencies),
            ),
            dependencies,
        )

        await service.create(
            SimpleNamespace(),
            SimpleNamespace(
                option=PaymentOptionDTO(
                    id=option_id,
                    label="Orcestr",
                    action_kind="solana_transaction_request",
                    amount=order.amount,
                    currency=order.currency,
                    payment_system="solana",
                    provider_kind="solana",
                ),
                public_base_url=dependencies.config.public_base_url,
                order=order,
                actor=SimpleNamespace(id=1),
                payment=SimpleNamespace(id=1, public_id=uuid4()),
            ),
        )

        assert intents.creation is not None
        settlement = intents.creation.settlement
        assert settlement.expected_raw_amount == "125123456"
        assert settlement.display_amount == "125.123456"
        assert settlement.quote.commercial_amount == "125.123456"
        assert settlement.quote.commercial_currency == "ORCESTR"
        assert settlement.quote.source == "order_snapshot"
        assert settlement.quote.rounding == "exact"

    @pytest.mark.asyncio
    async def test_cancel_returns_persisted_user_reason_to_commercexl(self, now) -> None:
        class CancelIntents:
            def __init__(self) -> None:
                self.reason_code = None
                self.clock = FrozenClock(now)

            async def get_by_payment(self, session, payment_id):
                _ = session
                _ = payment_id
                return SimpleNamespace(
                    cancel_reason="changed_mind",
                    cancel_idempotency_key="cancel-1",
                    cancel_actor_key="user:1",
                )

            async def cancel(self, session, payment_id, reason_code):
                _ = session
                _ = payment_id
                self.reason_code = reason_code

        intents = CancelIntents()
        config = SolanaCommerceConfig(
            public_base_url="https://pay.example.com",
            merchant_label="Merchant",
            enable_native_sol=True,
        )
        dependencies = SolanaProviderDependencies(
            config=config,
            assets=StaticAssetRegistry(()),
            recipients=StaticRecipientResolver({}),
            price_keys=ProductCodePriceKeyResolver(),
            quotes=FixedSettlementQuoteProvider({}),
            intents=intents,
        )
        registration = PaymentProviderRegistration(
            system="solana",
            provider_kind="solana",
            factory=lambda commerce, item: SolanaPaymentService(commerce, item, dependencies),
        )
        service = SolanaPaymentService(FakeCommerce(), registration, dependencies)

        result = await service.cancel(SimpleNamespace(), SimpleNamespace(id=7))

        assert result.reason_code == "changed_mind"
        assert intents.reason_code == "cancelled"

    @pytest.mark.asyncio
    async def test_direct_commercexl_cancel_fails_before_solana_row_mutation(self, now) -> None:
        class GuardedCancelIntents:
            def __init__(self) -> None:
                self.clock = FrozenClock(now)
                self.cancel_called = False

            async def get_by_payment(self, session, payment_id):
                _ = session
                _ = payment_id
                return SimpleNamespace(
                    cancel_reason=None,
                    cancel_idempotency_key=None,
                    cancel_actor_key=None,
                )

            async def cancel(self, session, payment_id, reason_code):
                _ = session
                _ = payment_id
                _ = reason_code
                self.cancel_called = True

        intents = GuardedCancelIntents()
        config = SolanaCommerceConfig(
            public_base_url="https://pay.example.com",
            merchant_label="Merchant",
            enable_native_sol=True,
        )
        dependencies = SolanaProviderDependencies(
            config=config,
            assets=StaticAssetRegistry(()),
            recipients=StaticRecipientResolver({}),
            price_keys=ProductCodePriceKeyResolver(),
            quotes=FixedSettlementQuoteProvider({}),
            intents=intents,
        )
        service = SolanaPaymentService(
            FakeCommerce(),
            PaymentProviderRegistration(
                system="solana",
                provider_kind="solana",
                factory=lambda commerce, item: SolanaPaymentService(commerce, item, dependencies),
            ),
            dependencies,
        )

        with pytest.raises(RuntimeError, match="SolanaApplicationService.cancel_intent"):
            await service.cancel(SimpleNamespace(), SimpleNamespace(id=7))

        assert intents.cancel_called is False

    @staticmethod
    def _token_asset() -> ValidatedSolanaAsset:
        return ValidatedSolanaAsset(
            policy=SolanaAssetPolicy(
                option_id="solana_orcestr",
                cluster="mainnet-beta",
                genesis_hash=MAINNET_GENESIS_HASH,
                kind=SolanaAssetKind.TOKEN,
                mint=ORCESTR_TOKEN_MINT,
                token_program=TOKEN_2022_PROGRAM_ID,
                decimals=6,
                display_name="Orcestr",
                symbol="ORCESTR",
            ),
            account_data_sha256="0" * 64,
        )

    @staticmethod
    def _service(config, asset, recipients, quotes, now) -> SolanaPaymentService:
        clock = FrozenClock(now)
        dependencies = SolanaProviderDependencies(
            config=config,
            assets=StaticAssetRegistry((asset,)),
            recipients=StaticRecipientResolver(recipients),
            price_keys=ProductCodePriceKeyResolver(),
            quotes=quotes,
            intents=SolanaIntentRepository(config, clock),
        )
        registration = PaymentProviderRegistration(
            system="solana",
            provider_kind="solana",
            factory=lambda commerce, item: SolanaPaymentService(commerce, item, dependencies),
        )
        return SolanaPaymentService(FakeCommerce(), registration, dependencies)
