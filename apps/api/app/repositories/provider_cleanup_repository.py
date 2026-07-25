from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider_cleanup_operation import ProviderCleanupOperation
from app.repositories.user_repository import UserRepository


class ProviderCleanupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id_for_update(
        self,
        operation_id: UUID,
    ) -> ProviderCleanupOperation | None:
        return await self.session.scalar(
            select(ProviderCleanupOperation)
            .where(ProviderCleanupOperation.id == operation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def get_by_resource(
        self,
        *,
        resource_type: str,
        provider_resource_id: str,
    ) -> ProviderCleanupOperation | None:
        return await self.session.scalar(
            select(ProviderCleanupOperation).where(
                ProviderCleanupOperation.resource_type == resource_type,
                ProviderCleanupOperation.provider_resource_id == provider_resource_id,
            )
        )

    async def list_incomplete_by_user_id(
        self,
        user_id: UUID,
    ) -> list[ProviderCleanupOperation]:
        return list(
            (
                await self.session.scalars(
                    select(ProviderCleanupOperation).where(
                        ProviderCleanupOperation.user_id == user_id,
                        ProviderCleanupOperation.completed_at.is_(None),
                    )
                )
            ).all()
        )

    async def list_incomplete_by_user_id_for_update(
        self,
        user_id: UUID,
    ) -> list[ProviderCleanupOperation]:
        return list(
            (
                await self.session.scalars(
                    select(ProviderCleanupOperation)
                    .where(
                        ProviderCleanupOperation.user_id == user_id,
                        ProviderCleanupOperation.completed_at.is_(None),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

    async def adopt(
        self,
        *,
        user_id: UUID,
        lifecycle_generation: int,
        resource_type: str,
        provider_resource_id: str,
    ) -> ProviderCleanupOperation:
        if resource_type not in {"phone_number", "stripe_subscription"}:
            raise ValueError("Unsupported provider cleanup resource")
        if not provider_resource_id:
            raise ValueError("Provider cleanup identity is required")
        owner = await UserRepository(self.session).get_by_id_for_update(user_id)
        if owner is None:
            raise ValueError("Provider cleanup owner does not exist")
        existing = await self.get_by_resource(
            resource_type=resource_type,
            provider_resource_id=provider_resource_id,
        )
        if existing is not None:
            if (
                existing.user_id != user_id
                or existing.lifecycle_generation != lifecycle_generation
            ):
                raise ValueError("Provider cleanup identity conflict")
            return existing
        operation = ProviderCleanupOperation(
            user_id=user_id,
            lifecycle_generation=lifecycle_generation,
            resource_type=resource_type,
            provider_resource_id=provider_resource_id,
            status="pending",
        )
        self.session.add(operation)
        await self.session.flush()
        return operation
