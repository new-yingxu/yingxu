from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit

DEFAULT_APP_VERSION = "0.9.0-local-sync-preview"
# The current source uses the local-agent-step and atomic-action migrations too.
# Refuse readiness on v14/v15 so the service cannot run against a partial schema.
REQUIRED_SCHEMA_VERSION = 16

# Tauri's packaged WebViews use platform-specific local origins. Keep these
# exact values in the production allowlist; do not allow arbitrary HTTP origins.
TAURI_PRODUCTION_ORIGINS = frozenset({"tauri://localhost", "http://tauri.localhost"})


def production_cors_origin_allowed(origin: str) -> bool:
    if origin in TAURI_PRODUCTION_ORIGINS:
        return True
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
            and "*" not in origin
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    auth_mode: str
    dev_auth_token: str | None
    jwt_issuer: str | None
    jwt_audience: str | None
    jwt_jwks_url: str | None
    cors_origins: tuple[str, ...]
    app_version: str
    cloudbase_env_id: str | None = None
    cloudbase_api_key: str | None = field(default=None, repr=False)

    model_owner_credentials: str | None = field(default=None, repr=False)

    cloudbase_client_origins: str | None = None
    session_private_key: str | None = field(default=None, repr=False)
    session_key_id: str | None = None
    session_verification_jwks: str | None = None
    baidu_client_id: str | None = None
    baidu_client_secret: str | None = field(default=None, repr=False)
    baidu_app_folder: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.getenv("APP_ENV", "development").lower()
        auth_mode = os.getenv("AUTH_MODE", "disabled").lower()
        database_url = os.getenv("DATABASE_URL", "sqlite:///./data/yingxu-cloud.db" if env == "development" else "")
        origins = tuple(x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip())
        settings = cls(
            app_env=env,
            database_url=database_url,
            auth_mode=auth_mode,
            dev_auth_token=os.getenv("DEV_AUTH_TOKEN"),
            jwt_issuer=os.getenv("JWT_ISSUER"),
            jwt_audience=os.getenv("JWT_AUDIENCE"),
            jwt_jwks_url=os.getenv("JWT_JWKS_URL"),
            cors_origins=origins,
            app_version=os.getenv("APP_VERSION", DEFAULT_APP_VERSION),
            cloudbase_env_id=os.getenv("CLOUDBASE_ENV_ID"),
            cloudbase_api_key=os.getenv("CLOUDBASE_APIKEY"),
            model_owner_credentials=os.getenv("MODEL_OWNER_CREDENTIALS"),
            cloudbase_client_origins=os.getenv("CLOUDBASE_CLIENT_ORIGINS"),
            session_private_key=os.getenv("SESSION_PRIVATE_KEY"),
            session_key_id=os.getenv("SESSION_KEY_ID"),
            session_verification_jwks=os.getenv("SESSION_VERIFICATION_JWKS"),
            baidu_client_id=os.getenv("BAIDU_CLIENT_ID"),
            baidu_client_secret=os.getenv("BAIDU_CLIENT_SECRET"),
            baidu_app_folder=os.getenv("BAIDU_APP_FOLDER"),
        )
        return settings

    def authentication_configuration_errors(self) -> list[str]:
        errors: list[str] = []
        if self.auth_mode == "dev" and (self.app_env != "development" or not self.dev_auth_token):
            errors.append("dev auth requires APP_ENV=development and an explicit DEV_AUTH_TOKEN")
        if self.auth_mode == "jwt" and not all((self.jwt_issuer, self.jwt_audience, self.jwt_jwks_url)):
            errors.append("jwt auth requires JWT_ISSUER, JWT_AUDIENCE, and JWT_JWKS_URL")
        if self.auth_mode == "cloudbase":
            if not self.cloudbase_env_id:
                errors.append("cloudbase auth requires CLOUDBASE_ENV_ID")
            try:
                import json
                clients=json.loads(self.cloudbase_client_origins or '{}')
                if not isinstance(clients,dict) or not clients or any(not isinstance(k,str) or not 1<=len(k)<=500 or v not in {'desktop','mobile'} for k,v in clients.items()):
                    raise ValueError()
            except (ValueError,TypeError):
                errors.append("cloudbase auth requires a valid CLOUDBASE_CLIENT_ORIGINS map")
            try:
                from .session_tokens import validate_signing_configuration
                validate_signing_configuration(self.session_private_key,self.session_key_id,self.jwt_issuer,self.jwt_audience,verification_jwks=json.loads(self.session_verification_jwks) if self.session_verification_jwks else None)
            except (ValueError,TypeError):
                errors.append("cloudbase auth requires valid session signing configuration")
            if self.app_env == 'production' and (not self.jwt_issuer or not self.jwt_issuer.startswith('https://')):
                errors.append("production session issuer must use HTTPS")
        if self.auth_mode not in {"disabled", "dev", "jwt", "cloudbase"}:
            errors.append("AUTH_MODE must be disabled, dev, jwt, or cloudbase")
        if self.app_env == "production" and self.auth_mode == "dev":
            errors.append("development auth is forbidden in production")
        return errors

    def authentication_configured(self) -> bool:
        return self.auth_mode != "disabled" and not self.authentication_configuration_errors()

    def configuration_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.database_url and not (self.cloudbase_env_id and self.cloudbase_api_key):
            errors.append("configure DATABASE_URL or CLOUDBASE_ENV_ID and CLOUDBASE_APIKEY")
        if self.app_env not in {"development", "staging", "production"}:
            errors.append("APP_ENV must be development, staging, or production")
        if self.app_env == "production" and self.database_url.startswith("sqlite:"):
            errors.append("production requires a shared database; SQLite is local-development only")
        errors.extend(self.authentication_configuration_errors())
        if self.app_env == "production" and not self.cors_origins:
            errors.append("production requires an explicit CORS_ORIGINS allowlist")
        if self.app_env == "production" and any("*" in origin for origin in self.cors_origins):
            errors.append("wildcard CORS origins are forbidden in production")
        if self.app_env == "production" and any(not production_cors_origin_allowed(origin) for origin in self.cors_origins):
            errors.append("production CORS origins must be HTTPS or an exact supported Tauri app origin")
        if self.app_env == "production" and self.auth_mode == "jwt" and self.jwt_jwks_url and not self.jwt_jwks_url.startswith("https://"):
            errors.append("production JWT JWKS URL must use HTTPS")
        return errors

