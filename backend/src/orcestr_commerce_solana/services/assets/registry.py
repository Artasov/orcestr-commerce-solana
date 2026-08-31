from __future__ import annotations

import hashlib
from typing import Protocol

from orcestr_commerce_solana.constants import LEGACY_TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID
from orcestr_commerce_solana.errors import SolanaAssetError, SolanaErrorCode
from orcestr_commerce_solana.rpc.protocol import SolanaRpc
from orcestr_commerce_solana.schemas.assets import (
    SolanaAssetKind,
    SolanaAssetPolicy,
    SolanaAssetStatus,
    SolanaCommitment,
    TokenExtensionSnapshot,
    ValidatedSolanaAsset,
)
from orcestr_commerce_solana.services.assets.codec import SolanaAddressCodec, Token2022Codec


class AssetRegistry(Protocol):
    """Resolves only host-approved immutable asset option IDs."""

    async def get(self, option_id: str) -> ValidatedSolanaAsset | None:
        """Returns one activated asset snapshot."""
        ...

    async def list_active(self) -> tuple[ValidatedSolanaAsset, ...]:
        """Returns activated options in deterministic order."""
        ...


class StaticAssetRegistry:
    """Stores validated host assets without accepting mint input from clients."""

    def __init__(self, assets: tuple[ValidatedSolanaAsset, ...]) -> None:
        by_id: dict[str, ValidatedSolanaAsset] = {}
        for asset in assets:
            if asset.policy.option_id in by_id:
                raise ValueError(f"Duplicate Solana asset option id: {asset.policy.option_id}.")
            by_id[asset.policy.option_id] = asset
        self._by_id = by_id

    async def get(self, option_id: str) -> ValidatedSolanaAsset | None:
        """Returns an asset only by its server-owned option identity."""
        return self._by_id.get(option_id)

    async def list_active(self) -> tuple[ValidatedSolanaAsset, ...]:
        """Returns active assets in option-id order."""
        return tuple(
            asset
            for _, asset in sorted(self._by_id.items())
            if asset.policy.status == SolanaAssetStatus.ACTIVE
        )


class SolanaAssetValidator:
    """Pins cluster, mint owner, decimals, authorities, and extension policy."""

    forbidden_mint_extensions = frozenset({1, 4, 9, 10, 12, 14, 16, 24, 25, 26})

    def __init__(self, rpc: SolanaRpc) -> None:
        self.rpc = rpc

    async def validate(self, policy: SolanaAssetPolicy) -> ValidatedSolanaAsset:
        """Validates an asset against finalized on-chain bytes before activation."""
        genesis_hash = await self.rpc.check_cluster()
        if genesis_hash != policy.genesis_hash:
            raise SolanaAssetError(SolanaErrorCode.WRONG_CLUSTER, "Asset policy does not match the connected cluster.")
        if policy.kind == SolanaAssetKind.NATIVE:
            return ValidatedSolanaAsset(policy=policy)

        mint = policy.mint
        if mint is None:
            raise SolanaAssetError(SolanaErrorCode.ASSET_NOT_FOUND, "Token asset has no mint.")
        SolanaAddressCodec.parse(mint)
        account = await self.rpc.get_account_info(mint, SolanaCommitment.FINALIZED)
        if account is None:
            raise SolanaAssetError(SolanaErrorCode.ASSET_NOT_FOUND, "Token mint account was not found.")
        if account.owner == LEGACY_TOKEN_PROGRAM_ID:
            raise SolanaAssetError(SolanaErrorCode.LEGACY_TOKEN_PROGRAM, "Legacy SPL Token Program is not supported.")
        if account.owner != TOKEN_2022_PROGRAM_ID:
            raise SolanaAssetError(SolanaErrorCode.WRONG_MINT_OWNER, "Mint is not owned by Token-2022.")

        mint_state = Token2022Codec.decode_mint(account.data)
        if mint_state.decimals != policy.decimals:
            raise SolanaAssetError(SolanaErrorCode.WRONG_DECIMALS, "Mint decimals do not match the configured snapshot.")
        if mint_state.mint_authority is not None and not policy.allow_mint_authority:
            raise SolanaAssetError(SolanaErrorCode.UNSAFE_MINT_AUTHORITY, "Active mint authority is not allowed by policy.")
        if mint_state.freeze_authority is not None and not policy.allow_freeze_authority:
            raise SolanaAssetError(SolanaErrorCode.UNSAFE_FREEZE_AUTHORITY, "Active freeze authority is not allowed by policy.")

        extensions: list[TokenExtensionSnapshot] = []
        for extension in mint_state.extensions:
            self._validate_mint_extension(extension.type_id, extension.data, policy)
            extensions.append(
                TokenExtensionSnapshot(
                    type_id=extension.type_id,
                    name=Token2022Codec.extension_name(extension.type_id),
                ),
            )
        return ValidatedSolanaAsset(
            policy=policy,
            mint_authority=mint_state.mint_authority,
            freeze_authority=mint_state.freeze_authority,
            extensions=tuple(extensions),
            account_data_sha256=hashlib.sha256(account.data).hexdigest(),
        )

    async def validate_recipient_token_account(
        self,
        asset: ValidatedSolanaAsset,
        recipient_wallet: str,
        token_account: str,
    ) -> bool:
        """Checks the exact initialized Token-2022 ATA used by a settlement snapshot."""
        policy = asset.policy
        if policy.kind != SolanaAssetKind.TOKEN or policy.mint is None:
            raise SolanaAssetError(SolanaErrorCode.INVALID_TOKEN_ACCOUNT, "Native SOL does not use token accounts.")
        SolanaAddressCodec.parse(recipient_wallet)
        SolanaAddressCodec.parse(token_account)
        expected = Token2022Codec.derive_associated_token_account(recipient_wallet, policy.mint)
        if token_account != expected:
            raise SolanaAssetError(SolanaErrorCode.INVALID_TOKEN_ACCOUNT, "Recipient token account is not the expected Token-2022 ATA.")
        account = await self.rpc.get_account_info(token_account, SolanaCommitment.FINALIZED)
        if account is None or account.owner != TOKEN_2022_PROGRAM_ID:
            raise SolanaAssetError(SolanaErrorCode.INVALID_TOKEN_ACCOUNT, "Recipient Token-2022 account was not found.")
        state = Token2022Codec.decode_token_account(account.data)
        if state.mint != policy.mint or state.owner != recipient_wallet or state.state != 1:
            raise SolanaAssetError(SolanaErrorCode.INVALID_TOKEN_ACCOUNT, "Recipient Token-2022 account identity or state is invalid.")
        for extension in state.extensions:
            if extension.type_id == 7:
                continue
            raise SolanaAssetError(
                SolanaErrorCode.UNSUPPORTED_TOKEN_EXTENSION,
                f"Recipient token account extension {Token2022Codec.extension_name(extension.type_id)} is unsupported.",
            )
        return True

    def _validate_mint_extension(self, type_id: int, data: bytes, policy: SolanaAssetPolicy) -> None:
        """Applies default-deny semantics to every decoded mint extension."""
        if type_id in self.forbidden_mint_extensions:
            raise SolanaAssetError(
                SolanaErrorCode.UNSUPPORTED_TOKEN_EXTENSION,
                f"Token-2022 mint extension {Token2022Codec.extension_name(type_id)} is unsupported.",
            )
        if type_id == 6:
            if data != b"\x01":
                raise SolanaAssetError(
                    SolanaErrorCode.UNSUPPORTED_TOKEN_EXTENSION,
                    "DefaultAccountState must be Initialized.",
                )
            return
        if type_id not in policy.allowed_mint_extensions:
            raise SolanaAssetError(
                SolanaErrorCode.UNSUPPORTED_TOKEN_EXTENSION,
                f"Token-2022 mint extension {Token2022Codec.extension_name(type_id)} is not allowlisted.",
            )
