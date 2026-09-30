from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.core.config import Settings, get_settings
from app.core.providers import Capability, Runs, describe_providers

router = APIRouter(prefix="/system", tags=["system"])


class ProviderStatus(BaseModel):
    capability: Capability
    provider: str
    model: str
    runs: Runs
    configured: bool


class ProvidersResponse(BaseModel):
    app: str
    tagline: str
    providers: list[ProviderStatus]


@router.get(
    "/providers",
    response_model=ProvidersResponse,
    summary="Which AI provider and model serves each capability (no secrets)",
    responses={401: {"description": "Missing, invalid, expired or revoked token"}},
)
async def providers(
    _: CurrentUser, settings: Annotated[Settings, Depends(get_settings)]
) -> ProvidersResponse:
    # Signed-in users only: model names and configuration gaps are not for anonymous visitors.
    return ProvidersResponse(
        app=settings.app_name,
        tagline=settings.app_tagline,
        providers=[ProviderStatus(**asdict(info)) for info in describe_providers(settings)],
    )
