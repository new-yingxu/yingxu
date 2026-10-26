from dataclasses import dataclass, field
import os


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
            app_version=os.getenv("APP_VERSION", "0.1.0-dev"),
            cloudbase_env_id=os.getenv("CLOUDBASE_ENV_ID"),
            cloudbase_api_key=os.getenv("CLOUDBASE_APIKEY"),
        )
        return settings

    def configuration_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.database_url and not (self.cloudbase_env_id and self.cloudbase_api_key):
            errors.append("configure DATABASE_URL or CLOUDBASE_ENV_ID and CLOUDBASE_APIKEY")
        if self.app_env not in {"development", "staging", "production"}:
            errors.append("APP_ENV must be development, staging, or production")
        if self.app_env == "production" and self.database_url.startswith("sqlite:"):
            errors.append("production requires a shared database; SQLite is local-development only")
        if self.auth_mode == "dev" and (self.app_env != "development" or not self.dev_auth_token):
            errors.append("dev auth requires APP_ENV=development and an explicit DEV_AUTH_TOKEN")
        if self.auth_mode == "jwt" and not all((self.jwt_issuer, self.jwt_audience, self.jwt_jwks_url)):
            errors.append("jwt auth requires JWT_ISSUER, JWT_AUDIENCE, and JWT_JWKS_URL")
        if self.auth_mode not in {"disabled", "dev", "jwt"}:
            errors.append("AUTH_MODE must be disabled, dev, or jwt")
        if self.app_env == "production" and self.auth_mode == "dev":
            errors.append("development auth is forbidden in production")
        if self.app_env == "production" and not self.cors_origins:
            errors.append("production requires an explicit CORS_ORIGINS allowlist")
        if self.app_env == "production" and any("*" in origin for origin in self.cors_origins):
            errors.append("wildcard CORS origins are forbidden in production")
        if self.app_env == "production" and any(not origin.startswith("https://") for origin in self.cors_origins):
            errors.append("production CORS origins must use HTTPS")
        if self.app_env == "production" and self.auth_mode == "jwt" and self.jwt_jwks_url and not self.jwt_jwks_url.startswith("https://"):
            errors.append("production JWT JWKS URL must use HTTPS")
        return errors

