from dataclasses import asdict

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.runtime import get_api_runtime
from app.auth.domain import AuthenticatedUser
from app.core.auth import require_user_identity
from app.core.config import Settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.schemas.dashboard import DashboardMetricsResponse
from app.services.dashboard_metrics_service import DashboardMetricsService


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def get_dashboard_metrics_service(
    session: AsyncSession = Depends(get_session),
) -> DashboardMetricsService:
    return DashboardMetricsService(session)


def get_dashboard_settings(request: Request) -> Settings:
    return get_api_runtime(request.app).settings


@router.get("/metrics", response_model=DashboardMetricsResponse)
@limiter.limit("60/minute")
async def get_dashboard_metrics(
    request: Request,
    identity: AuthenticatedUser = Depends(require_user_identity),
    service: DashboardMetricsService = Depends(get_dashboard_metrics_service),
    settings: Settings = Depends(get_dashboard_settings),
) -> DashboardMetricsResponse:
    metrics = await service.get_metrics(
        identity.internal_user_id,
        now=settings.dashboard_metrics_reference_time,
    )
    return DashboardMetricsResponse.model_validate(asdict(metrics))
