from pydantic import BaseModel, ConfigDict

from orcestr_commerce_solana.errors import SolanaErrorCode


class SolanaApiErrorDTO(BaseModel):
    """Returns one stable non-sensitive error from package-owned HTTP routes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: SolanaErrorCode
    message: str
