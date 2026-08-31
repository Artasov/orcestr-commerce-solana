from __future__ import annotations

from dataclasses import dataclass

from solders.pubkey import Pubkey

from orcestr_commerce_solana.constants import ASSOCIATED_TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID
from orcestr_commerce_solana.errors import SolanaAssetError, SolanaErrorCode


@dataclass(frozen=True)
class TokenExtension:
    """Contains one raw Token-2022 TLV extension."""

    type_id: int
    data: bytes


@dataclass(frozen=True)
class MintAccountState:
    """Contains consensus fields decoded directly from a Token-2022 mint."""

    mint_authority: str | None
    supply: int
    decimals: int
    is_initialized: bool
    freeze_authority: str | None
    extensions: tuple[TokenExtension, ...]


@dataclass(frozen=True)
class TokenAccountState:
    """Contains fields needed to validate a recipient Token-2022 account."""

    mint: str
    owner: str
    amount: int
    state: int
    extensions: tuple[TokenExtension, ...]


class SolanaAddressCodec:
    """Validates canonical 32-byte Solana public keys."""

    @staticmethod
    def parse(value: str) -> Pubkey:
        """Returns a pubkey or raises a typed safe error."""
        try:
            pubkey = Pubkey.from_string(value)
        except ValueError as exc:
            raise SolanaAssetError(SolanaErrorCode.INVALID_ADDRESS, "Invalid Solana public key.") from exc
        if str(pubkey) != value:
            raise SolanaAssetError(SolanaErrorCode.INVALID_ADDRESS, "Solana public key is not canonical.")
        return pubkey


class Token2022Codec:
    """Decodes fixed Token-2022 account prefixes and extension TLV records."""

    mint_base_size = 82
    token_account_base_size = 165
    extension_marker_offset = 165
    mint_account_type = 1
    token_account_type = 2

    extension_names = {
        0: "uninitialized",
        1: "transferFeeConfig",
        2: "transferFeeAmount",
        3: "mintCloseAuthority",
        4: "confidentialTransferMint",
        5: "confidentialTransferAccount",
        6: "defaultAccountState",
        7: "immutableOwner",
        8: "memoTransfer",
        9: "nonTransferable",
        10: "interestBearingConfig",
        11: "cpiGuard",
        12: "permanentDelegate",
        13: "nonTransferableAccount",
        14: "transferHook",
        15: "transferHookAccount",
        16: "confidentialTransferFeeConfig",
        17: "confidentialTransferFeeAmount",
        18: "metadataPointer",
        19: "tokenMetadata",
        20: "groupPointer",
        21: "tokenGroup",
        22: "groupMemberPointer",
        23: "tokenGroupMember",
        24: "confidentialMintBurn",
        25: "scaledUiAmount",
        26: "pausableConfig",
        27: "pausableAccount",
    }

    @classmethod
    def decode_mint(cls, data: bytes) -> MintAccountState:
        """Decodes Mint and refuses truncated or non-mint Token-2022 data."""
        if len(data) < cls.mint_base_size:
            raise SolanaAssetError(SolanaErrorCode.WRONG_MINT_OWNER, "Token-2022 mint account data is truncated.")
        mint_authority = cls._read_optional_pubkey(data, 0)
        supply = int.from_bytes(data[36:44], "little")
        decimals = data[44]
        is_initialized = data[45] == 1
        freeze_authority = cls._read_optional_pubkey(data, 46)
        if len(data) > cls.mint_base_size and any(data[cls.mint_base_size : cls.extension_marker_offset]):
            raise SolanaAssetError(SolanaErrorCode.UNSUPPORTED_TOKEN_EXTENSION, "Token-2022 mint padding is invalid.")
        extensions = cls._decode_extensions(
            data,
            cls.mint_base_size,
            cls.extension_marker_offset,
            cls.mint_account_type,
        )
        if not is_initialized:
            raise SolanaAssetError(SolanaErrorCode.WRONG_MINT_OWNER, "Token-2022 mint is not initialized.")
        return MintAccountState(
            mint_authority=mint_authority,
            supply=supply,
            decimals=decimals,
            is_initialized=is_initialized,
            freeze_authority=freeze_authority,
            extensions=extensions,
        )

    @classmethod
    def decode_token_account(cls, data: bytes) -> TokenAccountState:
        """Decodes a Token-2022 account used as the immutable recipient ATA."""
        if len(data) < cls.token_account_base_size:
            raise SolanaAssetError(SolanaErrorCode.INVALID_TOKEN_ACCOUNT, "Token-2022 account data is truncated.")
        mint = str(Pubkey.from_bytes(data[0:32]))
        owner = str(Pubkey.from_bytes(data[32:64]))
        amount = int.from_bytes(data[64:72], "little")
        state = data[108]
        extensions = cls._decode_extensions(
            data,
            cls.token_account_base_size,
            cls.extension_marker_offset,
            cls.token_account_type,
        )
        return TokenAccountState(mint=mint, owner=owner, amount=amount, state=state, extensions=extensions)

    @classmethod
    def extension_name(cls, type_id: int) -> str:
        """Returns a stable diagnostic name while keeping unknown IDs visible."""
        return cls.extension_names.get(type_id, f"unknown:{type_id}")

    @staticmethod
    def derive_associated_token_account(wallet: str, mint: str) -> str:
        """Derives an ATA with the Token-2022 program included in PDA seeds."""
        wallet_key = SolanaAddressCodec.parse(wallet)
        mint_key = SolanaAddressCodec.parse(mint)
        token_program = Pubkey.from_string(TOKEN_2022_PROGRAM_ID)
        associated_program = Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM_ID)
        address, _ = Pubkey.find_program_address(
            [bytes(wallet_key), bytes(token_program), bytes(mint_key)],
            associated_program,
        )
        return str(address)

    @staticmethod
    def _read_optional_pubkey(data: bytes, offset: int) -> str | None:
        """Decodes the COption<Pubkey> representation used by Mint."""
        option = int.from_bytes(data[offset : offset + 4], "little")
        if option == 0:
            return None
        if option != 1:
            raise SolanaAssetError(SolanaErrorCode.WRONG_MINT_OWNER, "Mint contains an invalid authority option.")
        return str(Pubkey.from_bytes(data[offset + 4 : offset + 36]))

    @classmethod
    def _decode_extensions(
        cls,
        data: bytes,
        no_extension_size: int,
        marker_offset: int,
        account_type: int,
    ) -> tuple[TokenExtension, ...]:
        """Walks Token-2022 TLV records and rejects malformed trailing data."""
        if len(data) == no_extension_size:
            return ()
        if len(data) < marker_offset + 1 or data[marker_offset] != account_type:
            raise SolanaAssetError(SolanaErrorCode.UNSUPPORTED_TOKEN_EXTENSION, "Token-2022 account type marker is invalid.")
        cursor = marker_offset + 1
        extensions: list[TokenExtension] = []
        seen: set[int] = set()
        while cursor < len(data):
            remainder = data[cursor:]
            if not any(remainder):
                break
            if len(remainder) < 4:
                raise SolanaAssetError(SolanaErrorCode.UNSUPPORTED_TOKEN_EXTENSION, "Token-2022 TLV header is truncated.")
            type_id = int.from_bytes(data[cursor : cursor + 2], "little")
            length = int.from_bytes(data[cursor + 2 : cursor + 4], "little")
            cursor += 4
            if type_id == 0 or type_id in seen or cursor + length > len(data):
                raise SolanaAssetError(SolanaErrorCode.UNSUPPORTED_TOKEN_EXTENSION, "Token-2022 TLV records are invalid.")
            extensions.append(TokenExtension(type_id=type_id, data=data[cursor : cursor + length]))
            seen.add(type_id)
            cursor += length
        return tuple(extensions)
