"""Additive browser contracts. Legacy token/SDK endpoints remain unchanged."""
import time
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import OperationalError

from app.api.deps import get_current_user, get_user_service
from app.core.browser_auth import get_browser_auth_settings
from app.core.config_store import get_settings
from app.models.user import User
from app.models.auth_session import AuthSession
from app.schemas.browser_auth import InstallationClaim, BrowserLogin, BrowserRegistration, BrowserRegistrationResponse, BrowserSessionResponse, SessionUser
from app.schemas.browser_auth import PublicBrowserSettings, CsrfResponse, AcceptedResponse, VerificationResponse, BrowserErrorResponse
from app.schemas.user import EmailVerificationRequest, EmailVerificationResendRequest
from app.services.auth_sessions import BrowserSessionService, SessionFailure
from app.services.user_service import UserService

logger = logging.getLogger(__name__)


class AuthRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def protected_response(request: Request):
            started = time.monotonic()
            try:
                response = await handler(request)
            except SessionFailure as exc:
                response = JSONResponse(status_code=exc.status, content={"detail": {"code": exc.code}}, headers={"Retry-After": str(exc.retry_after)} if exc.status == 429 else None)
            except OperationalError:
                response = JSONResponse(status_code=503, content={"detail": {"code": "unavailable"}})
            except RequestValidationError:
                # Default validation payloads may include password/token inputs.
                response = JSONResponse(status_code=422, content={"detail": {"code": "invalid_request"}})
            except HTTPException as exc:
                response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
            response.headers["Cache-Control"] = "no-store"
            logger.info("browser_auth method=%s path=%s status=%d duration_ms=%.1f", request.method, request.url.path, response.status_code, (time.monotonic() - started) * 1000)
            return response
        return protected_response


router = APIRouter(prefix="/auth", tags=["browser-auth"], route_class=AuthRoute, responses={code: {"model": BrowserErrorResponse} for code in (400, 401, 403, 409, 415, 422, 429, 503)})


def browser_service(service: UserService = Depends(get_user_service)) -> BrowserSessionService:
    try:
        settings = get_browser_auth_settings()
    except ValueError as exc:
        raise SessionFailure("unavailable", 503) from exc
    if not settings.enabled:
        raise SessionFailure("unavailable", 503)
    jwt_settings = get_settings()
    if settings.secure_cookies and not (jwt_settings.jwt_private_key_pem and jwt_settings.jwt_public_key_pem):
        raise SessionFailure("unavailable", 503)
    return BrowserSessionService(service.session, settings)


def session_id(request: Request, service: BrowserSessionService) -> str:
    return request.cookies.get(service.settings.refresh_cookie, "").partition(".")[0]


def protect(request: Request, service: BrowserSessionService) -> None:
    if request.headers.get("origin") not in service.settings.allowed_origins:
        raise SessionFailure("csrf_invalid", 403)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise SessionFailure("invalid_request", 415)
    service.validate_csrf(request.cookies.get(service.settings.csrf_cookie, ""), request.headers.get("x-csrf-token", ""), session_id(request, service))


def set_csrf(response: Response, service: BrowserSessionService, token: str) -> None:
    response.set_cookie(service.settings.csrf_cookie, token, max_age=600, secure=service.settings.secure_cookies, httponly=True, samesite="lax", path="/")


def set_session(response: Response, service: BrowserSessionService, result: tuple[BrowserSessionResponse, str, AuthSession]) -> BrowserSessionResponse:
    data, credential, row = result
    maximum = max(0, row.absolute_expires_at - int(time.time())) if row.persistent else None
    response.set_cookie(service.settings.refresh_cookie, credential, max_age=maximum, secure=service.settings.secure_cookies, httponly=True, samesite="lax", path="/")
    set_csrf(response, service, data.csrf_token)
    return data


def remote_address(request: Request) -> str:
    # Never trust user-supplied X-Forwarded-For. Configure ASGI trusted proxies.
    return request.client.host if request.client else "unknown"


@router.get("/public", response_model=PublicBrowserSettings)
async def public_settings(service: BrowserSessionService = Depends(browser_service)):
    settings = get_settings()
    return {"user_creation_mode": settings.user_creation_mode, "email_verification_required": settings.email_verification_enabled, "password_min_length": 12, "persistent_days": service.settings.persistent_days, "persistent_idle_days": service.settings.persistent_idle_days}


@router.get("/csrf", response_model=CsrfResponse)
async def csrf(request: Request, response: Response, service: BrowserSessionService = Depends(browser_service)):
    token = service.csrf(session_id(request, service), request.cookies.get(service.settings.csrf_cookie))
    set_csrf(response, service, token)
    return {"csrf_token": token}


@router.post("/login", response_model=BrowserSessionResponse)
async def login(payload: BrowserLogin, request: Request, response: Response, users: UserService = Depends(get_user_service), service: BrowserSessionService = Depends(browser_service)):
    protect(request, service)
    await service.limit("login-ip", remote_address(request), 30)
    await service.limit("login-account", payload.identifier.strip().casefold(), 10)
    user, failure = await users.authenticate_user_with_reason(payload.identifier.strip(), payload.password)
    if not user:
        raise SessionFailure(failure or "invalid_credentials", 401 if failure == "invalid_credentials" else 403)
    return set_session(response, service, await service.create(user, payload.remember_me, request.cookies.get(service.settings.refresh_cookie, "")))


@router.post("/refresh", response_model=BrowserSessionResponse)
async def refresh(request: Request, response: Response, service: BrowserSessionService = Depends(browser_service)):
    protect(request, service)
    credential = request.cookies.get(service.settings.refresh_cookie, "")
    request_id = request.headers.get("x-request-id", "")
    try:
        UUID(request_id)
    except ValueError as exc:
        raise SessionFailure("invalid_request", 422) from exc
    await service.limit("refresh-ip", remote_address(request), 120)
    await service.limit("refresh", session_id(request, service) or remote_address(request), 60)
    return set_session(response, service, await service.refresh(credential, request_id))


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, service: BrowserSessionService = Depends(browser_service)):
    protect(request, service)
    credential = request.cookies.get(service.settings.refresh_cookie, "")
    await service.revoke_credential(credential)
    await service.db.commit()
    for name in (service.settings.refresh_cookie, service.settings.csrf_cookie):
        response.delete_cookie(name, path="/", secure=service.settings.secure_cookies, httponly=True, samesite="lax")


@router.get("/session", response_model=SessionUser)
async def session_user(user: User = Depends(get_current_user)):
    """Identity consumers use this bearer endpoint to enforce sid revocation."""
    return SessionUser.model_validate(user)


@router.post("/register", status_code=201, response_model=BrowserRegistrationResponse)
async def register(payload: BrowserRegistration, request: Request, response: Response, service: BrowserSessionService = Depends(browser_service)):
    protect(request, service)
    await service.limit("register", remote_address(request), 5, 300)
    result, issued = await service.register(payload, request.cookies.get(service.settings.refresh_cookie, ""))
    if issued:
        set_session(response, service, issued)
    return result


@router.post("/verify-email", response_model=VerificationResponse)
async def verify_email(payload: EmailVerificationRequest, request: Request, users: UserService = Depends(get_user_service), service: BrowserSessionService = Depends(browser_service)):
    protect(request, service)
    await service.limit("verification", remote_address(request), 20)
    if not await users.verify_email(payload.token):
        raise SessionFailure("verification_invalid", 400)
    return {"verified": True}


@router.post("/resend-verification", response_model=AcceptedResponse)
async def resend(payload: EmailVerificationResendRequest, request: Request, users: UserService = Depends(get_user_service), service: BrowserSessionService = Depends(browser_service)):
    protect(request, service)
    await service.limit("resend-ip", remote_address(request), 10, 300)
    await service.limit("resend-email", str(payload.email).casefold(), 1, 60)
    await users.resend_verification(str(payload.email))
    return {"accepted": True}


@router.post("/claim", status_code=201, response_model=BrowserRegistrationResponse)
async def claim_installation(payload: InstallationClaim, request: Request, response: Response, service: BrowserSessionService = Depends(browser_service)):
    protect(request, service)
    from app.core.installation_claim import claim_required, resolve_claim_token
    if not claim_required():
        raise SessionFailure("installation_disabled", 404)
    await service.limit("installation-claim", remote_address(request), 5, 300)
    token = resolve_claim_token(payload.claim_token, request.headers.get("X-Shrecknet-Installation-Claim"))
    if not token:
        raise SessionFailure("installation_claim_required", 403)
    result, issued = await service.register(payload, request.cookies.get(service.settings.refresh_cookie, ""), installation_claim=token)
    if issued:
        set_session(response, service, issued)
    return result
