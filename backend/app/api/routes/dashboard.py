from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.dashboard import DashboardResponse
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get(
    "",
    response_model=DashboardResponse,
    summary="Your workspace totals, chat AI usage over 30 days, and recent activity",
    responses={401: {"description": "Missing, invalid, expired or revoked token"}},
)
async def dashboard(db: DbSession, current_user: CurrentUser) -> DashboardResponse:
    return await dashboard_service.get_dashboard(db, current_user.id)
