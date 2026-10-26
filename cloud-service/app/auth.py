from dataclasses import dataclass
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from jwt import PyJWKClient
from hmac import compare_digest

from .config import Settings

bearer = HTTPBearer(auto_error=False)
_jwks_clients: dict[str, PyJWKClient] = {}


@dataclass(frozen=True)
class Principal:
    owner_id: str
    device_id: str
    origin: str


class AuthConfigurationError(ValueError):
    pass


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def principal_from_token(token: str, settings: Settings) -> Principal:
    if settings.configuration_errors() or settings.auth_mode == "disabled":
        raise AuthConfigurationError("authentication configuration is not ready")
    if settings.auth_mode == "dev":
        if settings.dev_auth_token and compare_digest(token.encode(), settings.dev_auth_token.encode()) and settings.app_env == "development":
            return Principal(owner_id="local-dev-owner", device_id="local-dev-device", origin="desktop")
        raise ValueError("invalid development token")
    if settings.auth_mode != "jwt" or not settings.jwt_jwks_url:
        raise ValueError("authentication provider is not configured")
    client = _jwks_clients.setdefault(settings.jwt_jwks_url, PyJWKClient(settings.jwt_jwks_url, cache_keys=True))
    key = client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key.key,
        algorithms=["RS256", "ES256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={"require": ["sub", "iss", "aud", "exp"]},
    )
    owner_id = claims.get("sub")
    device_id = claims.get("device_id")
    origin = claims.get("origin")
    if not all(isinstance(x, str) and x.strip() for x in (owner_id, device_id)) or origin not in {"desktop", "mobile"}:
        raise ValueError("required identity claims missing")
    if len(owner_id) > 160 or len(device_id) > 500:
        raise ValueError("identity claim exceeds storage limit")
    return Principal(owner_id=owner_id, device_id=device_id, origin=origin)


def require_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if credentials is None:
        raise HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED"})
    token = credentials.credentials
    try:
        return principal_from_token(token, settings)
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail={"code": "AUTH_NOT_CONFIGURED"}) from exc
    except Exception as exc:
        request.app.state.logger.warning("token validation failed", extra={"reason": type(exc).__name__})
        raise HTTPException(status_code=401, detail={"code": "INVALID_SESSION"}) from exc

