from importlib.metadata import version

import orcestr_commerce_solana
from orcestr_commerce_solana import __version__


def test_public_version_matches_distribution_metadata() -> None:
    """Prevents release metadata and the runtime public version from diverging."""
    assert __version__ == version("orcestr-commerce-solana")


def test_legacy_token_program_is_not_a_public_root_export() -> None:
    assert not hasattr(orcestr_commerce_solana, "LEGACY_TOKEN_PROGRAM_ID")
