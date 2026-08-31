from __future__ import annotations

import base64

import pytest

from conftest import FakeRpc, FixtureLoader
from orcestr_commerce_solana.amounts import SolanaAmountCodec
from orcestr_commerce_solana.constants import (
    LEGACY_TOKEN_PROGRAM_ID,
    MAINNET_GENESIS_HASH,
    ORCESTR_TOKEN_MINT,
    TOKEN_2022_PROGRAM_ID,
)
from orcestr_commerce_solana.errors import SolanaAssetError, SolanaCommerceError, SolanaErrorCode
from orcestr_commerce_solana.rpc.types import RpcAccountInfo
from orcestr_commerce_solana.schemas.assets import SolanaAssetKind, SolanaAssetPolicy
from orcestr_commerce_solana.services.assets.codec import Token2022Codec
from orcestr_commerce_solana.services.assets.registry import SolanaAssetValidator


class TestSolanaAmountCodec:
    """Covers exact display and u64 conversion boundaries."""

    def test_round_trip_without_float_or_exponent(self) -> None:
        raw = SolanaAmountCodec.parse("806981765.769894", 6)

        assert raw == 806_981_765_769_894
        assert SolanaAmountCodec.format(raw, 6) == "806981765.769894"
        assert SolanaAmountCodec.format(1_000_000, 6) == "1"

    @pytest.mark.parametrize("value", ["1e3", "-1", "+1", "01", "1.0000001", " 1"])
    def test_rejects_noncanonical_display_amounts(self, value: str) -> None:
        with pytest.raises(SolanaCommerceError) as error:
            SolanaAmountCodec.parse(value, 6)

        assert error.value.code == SolanaErrorCode.INVALID_AMOUNT


class TestSolanaAssetValidator:
    """Pins the production ORCESTR mint and Token-2022 default-deny policy."""

    @pytest.mark.asyncio
    async def test_validates_frozen_orcestr_mint_fixture(self) -> None:
        fixture = FixtureLoader.load("orcestr_mint_account.json")
        rpc = FakeRpc()
        rpc.accounts[fixture["address"]] = RpcAccountInfo(
            owner=fixture["owner"],
            lamports=fixture["lamports"],
            executable=fixture["executable"],
            rent_epoch=fixture["rent_epoch"],
            data=base64.b64decode(fixture["data_base64"]),
        )
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

        asset = await SolanaAssetValidator(rpc).validate(policy)

        assert asset.mint_authority is None
        assert asset.freeze_authority is None
        assert [item.type_id for item in asset.extensions] == fixture["expected"]["extension_ids"]
        assert asset.account_data_sha256 == fixture["expected"]["account_data_sha256"]

    @pytest.mark.asyncio
    async def test_legacy_token_owner_is_always_explicitly_rejected(self) -> None:
        fixture = FixtureLoader.load("legacy_token_mint.json")
        rpc = FakeRpc()
        rpc.accounts[fixture["address"]] = RpcAccountInfo(
            owner=LEGACY_TOKEN_PROGRAM_ID,
            lamports=1,
            executable=False,
            rent_epoch=0,
            data=base64.b64decode(fixture["data_base64"]),
        )
        policy = SolanaAssetPolicy(
            option_id="legacy_rejected",
            cluster="mainnet-beta",
            genesis_hash=MAINNET_GENESIS_HASH,
            kind=SolanaAssetKind.TOKEN,
            mint=fixture["address"],
            token_program=TOKEN_2022_PROGRAM_ID,
            decimals=9,
            display_name="Wrapped SOL",
            symbol="SOL",
        )

        with pytest.raises(SolanaAssetError) as error:
            await SolanaAssetValidator(rpc).validate(policy)

        assert error.value.code.value == fixture["expected_reason"]

    @pytest.mark.asyncio
    async def test_transfer_fee_extension_is_default_denied(self) -> None:
        data = bytearray(166 + 4 + 108)
        data[44] = 6
        data[45] = 1
        data[165] = 1
        data[166:168] = (1).to_bytes(2, "little")
        data[168:170] = (108).to_bytes(2, "little")
        rpc = FakeRpc()
        rpc.accounts[ORCESTR_TOKEN_MINT] = RpcAccountInfo(
            owner=TOKEN_2022_PROGRAM_ID,
            lamports=1,
            executable=False,
            rent_epoch=0,
            data=bytes(data),
        )
        policy = SolanaAssetPolicy(
            option_id="fee_token",
            cluster="mainnet-beta",
            genesis_hash=MAINNET_GENESIS_HASH,
            kind=SolanaAssetKind.TOKEN,
            mint=ORCESTR_TOKEN_MINT,
            token_program=TOKEN_2022_PROGRAM_ID,
            decimals=6,
            display_name="Fee token",
            symbol="FEE",
            allowed_mint_extensions=frozenset({1}),
        )

        with pytest.raises(SolanaAssetError) as error:
            await SolanaAssetValidator(rpc).validate(policy)

        assert error.value.code == SolanaErrorCode.UNSUPPORTED_TOKEN_EXTENSION

    @pytest.mark.asyncio
    async def test_recipient_memo_transfer_extension_is_rejected_in_v1(self) -> None:
        from solders.pubkey import Pubkey
        from orcestr_commerce_solana.schemas.assets import ValidatedSolanaAsset

        rpc = FakeRpc()
        wallet = str(Pubkey.new_unique())
        token_account = Token2022Codec.derive_associated_token_account(wallet, ORCESTR_TOKEN_MINT)
        data = bytearray(166 + 5)
        data[0:32] = bytes(Pubkey.from_string(ORCESTR_TOKEN_MINT))
        data[32:64] = bytes(Pubkey.from_string(wallet))
        data[108] = 1
        data[165] = 2
        data[166:168] = (8).to_bytes(2, "little")
        data[168:170] = (1).to_bytes(2, "little")
        data[170] = 1
        rpc.accounts[token_account] = RpcAccountInfo(
            owner=TOKEN_2022_PROGRAM_ID,
            lamports=1,
            executable=False,
            rent_epoch=0,
            data=bytes(data),
        )
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

        with pytest.raises(SolanaAssetError) as error:
            await SolanaAssetValidator(rpc).validate_recipient_token_account(
                ValidatedSolanaAsset(policy=policy, account_data_sha256="0" * 64),
                wallet,
                token_account,
            )

        assert error.value.code == SolanaErrorCode.UNSUPPORTED_TOKEN_EXTENSION
