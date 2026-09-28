from fastapi import APIRouter, Request, Response, status

from app.api.deps import CurrentToken, CurrentUser, DbSession
from app.core import rate_limit
from app.core.rate_limit import Scope
from app.core.security import create_access_token
from app.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserRead
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

_AUTH_ERRORS = {401: {"description": "Missing, invalid, expired or revoked token"}}


def _token_response(user: User) -> TokenResponse:
    token, expires_in = create_access_token(user.id)
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserRead.model_validate(user))


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and return an access token",
    responses={409: {"description": "Email already registered"}, 429: {"description": "Too many sign-ups"}},
)
async def register(data: RegisterRequest, db: DbSession, request: Request) -> TokenResponse:
    await rate_limit.enforce(Scope.REGISTER, rate_limit.client_address(request))
    user = await auth_service.register_user(db, data)
    return _token_response(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange email and password for an access token",
    responses={
        401: {"description": "Invalid email or password"},
        403: {"description": "Account disabled"},
        429: {"description": "Too many attempts for this account from this address"},
    },
)
async def login(data: LoginRequest, db: DbSession, request: Request) -> TokenResponse:
    # Per address and account: slows password guessing without letting one client lock
    # a user out from everywhere.
    await rate_limit.enforce(Scope.LOGIN, f"{rate_limit.client_address(request)}:{data.email.lower()}")
    user = await auth_service.authenticate_user(db, data)
    return _token_response(user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current access token",
    responses=_AUTH_ERRORS,
)
async def logout(token: CurrentToken, db: DbSession) -> Response:
    await auth_service.revoke_token(db, token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserRead, summary="Get the current user", responses=_AUTH_ERRORS)
async def me(current_user: CurrentUser) -> User:
    return current_user
