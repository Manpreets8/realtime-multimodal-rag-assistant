from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from app.api.deps import CurrentToken, CurrentUser, DbSession
from app.core import rate_limit
from app.core.rate_limit import Scope
from app.core.security import create_access_token
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    RegisterRequest,
    TokenResponse,
    UserRead,
)
from app.services import auth_service, email_service

router = APIRouter(prefix="/auth", tags=["auth"])

_AUTH_ERRORS = {401: {"description": "Missing, invalid, expired or revoked token"}}


def _token_response(user: User) -> TokenResponse:
    token, expires_in = create_access_token(user.id, user.session_version)
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserRead.model_validate(user))


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and return an access token",
    responses={409: {"description": "Email already registered"}, 429: {"description": "Too many sign-ups"}},
)
async def register(
    data: RegisterRequest, db: DbSession, request: Request, background_tasks: BackgroundTasks
) -> TokenResponse:
    await rate_limit.enforce(Scope.REGISTER, rate_limit.client_address(request))
    user = await auth_service.register_user(db, data)
    # Sent after the response: the mail server's speed or availability never affects sign-up.
    background_tasks.add_task(email_service.send_welcome_email, user.id, user.email, user.full_name)
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


@router.patch("/me", response_model=UserRead, summary="Update your profile", responses=_AUTH_ERRORS)
async def update_me(data: ProfileUpdate, current_user: CurrentUser, db: DbSession) -> User:
    return await auth_service.update_profile(db, current_user, data)


@router.post(
    "/change-password",
    response_model=TokenResponse,
    summary="Change your password; signs out every other session and returns a new token",
    responses={
        **_AUTH_ERRORS,
        400: {"description": "Current password is incorrect, or the new one is the same"},
        429: {"description": "Too many attempts"},
    },
)
async def change_password(data: PasswordChange, current_user: CurrentUser, db: DbSession) -> TokenResponse:
    # Same budget as logins: this endpoint checks a password too.
    await rate_limit.enforce(Scope.LOGIN, f"password-change:{current_user.id}")
    user = await auth_service.change_password(db, current_user, data)
    return _token_response(user)


@router.post(
    "/logout-all",
    response_model=TokenResponse,
    summary="Sign out every other session; returns a new token for this one",
    responses=_AUTH_ERRORS,
)
async def logout_all(current_user: CurrentUser, db: DbSession) -> TokenResponse:
    user = await auth_service.end_other_sessions(db, current_user)
    return _token_response(user)
