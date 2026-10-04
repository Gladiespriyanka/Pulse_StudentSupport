"""Central configuration. Everything is read from environment variables (.env in dev).

Production safety: `Settings.validate_for_environment()` fails fast at start-up when
insecure defaults are used or when Demo Mode is enabled in production.
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_JWT_DEFAULT = "dev-only-insecure-jwt-secret-change-me-0123456789"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "PULSE"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://pulse:pulse@localhost:5432/pulse_dev"

    # --- auth -----------------------------------------------------------------
    jwt_secret: str = INSECURE_JWT_DEFAULT
    jwt_issuer: str = "pulse"
    jwt_audience: str = "pulse-web"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False  # MUST be true in production (HTTPS)
    cookie_samesite: Literal["lax", "strict"] = "lax"
    cookie_domain: str | None = None
    login_max_attempts: int = 5
    lockout_minutes: int = 15
    password_min_length: int = 10

    # --- data protection --------------------------------------------------------
    # Comma separated Fernet keys. First key encrypts, all keys can decrypt (rotation).
    field_encryption_keys: str = ""

    # --- http -------------------------------------------------------------------
    cors_origins: str = ""  # same-origin deployment by default; comma separated if needed
    docs_enabled: bool = True  # /api/docs, /api/redoc, /api/openapi.json
    serve_frontend: bool = False  # dev convenience; nginx serves it in Docker
    frontend_dir: str = "../frontend"
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 300  # per client IP, all API calls
    login_rate_limit_per_minute: int = 10  # per IP+email

    # --- workflow tunables --------------------------------------------------------
    sla_sweep_interval_seconds: int = 60
    auto_close_resolved_days: int = 7
    break_glass_hours: int = 48
    aggregate_min_cell: int = 5  # cells below this are masked for sensitive categories
    cluster_min_requests: int = 3
    cluster_window_days: int = 14
    problem_min_incidents: int = 2
    problem_window_days: int = 60
    case_window_days: int = 21
    case_min_requests: int = 3
    case_min_departments: int = 3
    import_max_rows: int = 5000
    import_max_bytes: int = 2_000_000
    campus_timezone: str = "Asia/Kolkata"
    crisis_resources: str = "Emergency: 112 | Tele-MANAS (24x7 mental health support): 14416"

    # --- demo mode (development / testing only) -------------------------------------
    demo_mode: bool = False
    demo_password: str = "PulseDemo#2026"

    # --- first admin bootstrap (used by `python -m app.cli bootstrap-admin`) -----------
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_name: str = "System Administrator"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def crisis_resource_list(self) -> list[str]:
        return [s.strip() for s in self.crisis_resources.split("|") if s.strip()]

    def fernet_keys(self) -> list[str]:
        keys = [k.strip() for k in self.field_encryption_keys.split(",") if k.strip()]
        if keys:
            return keys
        # Dev/test only: derive a stable key from the JWT secret so local data stays readable.
        digest = hashlib.sha256(("fernet:" + self.jwt_secret).encode()).digest()
        return [base64.urlsafe_b64encode(digest).decode()]

    def validate_for_environment(self) -> None:
        problems: list[str] = []
        if self.demo_mode and self.is_production:
            problems.append("DEMO_MODE=true is not allowed when APP_ENV=production")
        if self.is_production:
            if self.jwt_secret == INSECURE_JWT_DEFAULT or len(self.jwt_secret) < 32:
                problems.append("JWT_SECRET must be a unique random value of at least 32 characters")
            if "change" in self.jwt_secret.lower():
                problems.append("JWT_SECRET still contains a placeholder (generate with `python -m app.cli gen-keys`)")
            if not self.field_encryption_keys.strip():
                problems.append("FIELD_ENCRYPTION_KEYS must be set (generate with `python -m app.cli gen-keys`)")
            else:
                from cryptography.fernet import Fernet

                for k in self.field_encryption_keys.split(","):
                    try:
                        Fernet(k.strip().encode())
                    except Exception:
                        problems.append("FIELD_ENCRYPTION_KEYS contains an invalid Fernet key")
                        break
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true (serve over HTTPS)")
            if self.serve_frontend:
                problems.append("SERVE_FRONTEND must be false in production (use the nginx container)")
            dbname = self.database_url.lower().rsplit("/", 1)[-1].split("?")[0]
            if any(t in dbname for t in ("demo", "dev", "test")):
                problems.append("DATABASE_URL points at a demo/dev/test database name")
        if problems:
            raise RuntimeError("Unsafe configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.validate_for_environment()
    return s


def reset_settings_cache() -> None:  # used by tests
    get_settings.cache_clear()
