from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CommercePaymentUpdatedEvent(BaseModel):
    """Invalidates authoritative REST state through the host shared WebSocket."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event: str = Field(default="commerce.payment.updated", pattern=r"^commerce\.payment\.updated$")
    order_public_id: UUID
    payment_public_id: UUID
    revision: int = Field(ge=0)
