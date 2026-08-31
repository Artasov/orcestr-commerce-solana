from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orcestr_commerce_solana.models import SolanaPaymentIntentORM, SolanaTransferORM


class SqlAlchemyUsedSignatureRegistry:
    """Checks signature ownership inside the current host database transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_used(self, cluster: str, signature: str, intent_public_id: UUID) -> bool:
        """Returns whether any persisted intent already claimed this transaction."""
        query = (
            select(SolanaPaymentIntentORM.public_id)
            .join(SolanaTransferORM, SolanaTransferORM.intent_id == SolanaPaymentIntentORM.id)
            .where(
                SolanaTransferORM.cluster == cluster,
                SolanaTransferORM.signature == signature,
            )
        )
        claimed_intent = (await self.session.execute(query)).scalar_one_or_none()
        return claimed_intent is not None and claimed_intent != intent_public_id


class SignatureSessionFactory(Protocol):
    """Creates short-lived read sessions for background verification."""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Returns a host-owned async session context."""
        ...


class SessionFactoryUsedSignatureRegistry:
    """Checks persisted signature ownership from a background reconciler."""

    def __init__(self, session_factory: SignatureSessionFactory) -> None:
        self.session_factory = session_factory

    async def is_used(self, cluster: str, signature: str, intent_public_id: UUID) -> bool:
        """Uses a fresh session so the verifier never owns transaction lifetime."""
        async with self.session_factory() as session:
            registry = SqlAlchemyUsedSignatureRegistry(session)
            return await registry.is_used(cluster, signature, intent_public_id)
