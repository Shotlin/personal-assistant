"""Application settings loaded from environment / `.env`.

Validation is intentional: an invalid configuration must fail before the
application serves any traffic (Phase 1 spec, Task 1 acceptance).
"""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_MODEL_PROVIDERS = {"openrouter", "openai", "generic_openai_compatible"}
SUPPORTED_APP_ENVS = {"development", "production"}
SUPPORTED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class SettingsError(RuntimeError):
    """Raised when the configuration is invalid; startup must fail."""


class Settings(BaseSettings):
    """Validated Phase 1 configuration surface (spec section 9)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8787
    log_level: str = "INFO"

    # Gateway identity
    agent_gateway_api_key: str = "change-me"
    assistant_model_id: str = "personal-assistant-v1"
    assistant_id: str = "personal-assistant"

    # Model provider selection
    model_provider: str = "openrouter"
    model_name: str = "openrouter/auto"
    model_api_key: str = ""
    model_base_url: str = ""
    model_timeout_seconds: int = 120
    model_max_retries: int = 2
    model_max_tokens: int = 2000
    model_reasoning_effort: str = "low"

    # Provider keys -- never logged, never returned to clients.
    openrouter_api_key: str = ""
    openai_api_key: str = ""

    # Persistence
    database_url: str = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"

    # CUA (computer use)
    cua_enabled: bool = True
    cua_command: str = "cua-driver"
    cua_permission_mode: str = "bounded"
    cua_capability_manifest_path: str = ""
    cua_existing_profile_grant: bool = False
    cua_artifact_dir: str = "var/artifacts"

    # Open WebUI integration
    openwebui_forward_headers: bool = True

    # WP3: trusted desktop session ownership (feature flag, master plan 17).
    # false disables cursor sessions entirely: runs execute without a
    # visible agent cursor and the driver is never contacted for sessions.
    active_cursor_persistence_enabled: bool = True
    # Deterministic progress text in the SSE stream (WP3 section 7.4);
    # status lines are interface output only, never model history.
    status_events_enabled: bool = True
    status_quiet_seconds: float = 6.0
    # WP6: compact same-model planner route (natural phrasing -> one model
    # decision -> local recipe). Feature flag for staged rollout and
    # rollback (master plan 15.2/WP8): false sends every non-exact turn to
    # the general agent exactly as before WP6.
    compact_planner_enabled: bool = False

    # Agent Designer (frozen plan v5.1, C2 + Safety note 1). Default false:
    # the legacy Phase-1 path is the rollback boundary. Designer-only
    # settings below are validated ONLY when this is true; missing or
    # invalid values never block a flag-off startup.
    designer_enabled: bool = False
    designer_credentials_key: str = ""
    designer_openwebui_base_url: str = "http://127.0.0.1:3000"
    designer_session_ttl_minutes: int = 720
    designer_login_max_attempts: int = 10
    designer_cookie_secure: bool = False
    # Shared secret between the same-origin reverse proxy and the gateway
    # (SSO Mode C). Empty disables SSO entirely — the standalone Designer
    # login flow is then the only path.
    designer_proxy_key: str = ""


    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        errors: list[str] = []

        if self.app_env not in SUPPORTED_APP_ENVS:
            allowed = sorted(SUPPORTED_APP_ENVS)
            errors.append(f"APP_ENV must be one of {allowed}, got {self.app_env!r}")
        if self.log_level.upper() not in SUPPORTED_LOG_LEVELS:
            allowed = sorted(SUPPORTED_LOG_LEVELS)
            errors.append(f"LOG_LEVEL must be one of {allowed}, got {self.log_level!r}")
        if not 1 <= self.app_port <= 65535:
            errors.append(f"APP_PORT must be 1-65535, got {self.app_port}")
        if self.model_timeout_seconds <= 0:
            errors.append(f"MODEL_TIMEOUT_SECONDS must be > 0, got {self.model_timeout_seconds}")
        if self.model_max_retries < 0:
            errors.append(f"MODEL_MAX_RETRIES must be >= 0, got {self.model_max_retries}")
        if self.model_reasoning_effort not in {"", "default", "low", "medium", "high"}:
            errors.append(
                f"MODEL_REASONING_EFFORT must be one of [default, low, medium, high], "
                f"got {self.model_reasoning_effort!r}"
            )
        if self.model_max_tokens < 200:
            errors.append(f"MODEL_MAX_TOKENS must be >= 200, got {self.model_max_tokens}")
        if self.status_quiet_seconds <= 0:
            errors.append(
                f"STATUS_QUIET_SECONDS must be > 0, got {self.status_quiet_seconds}"
            )

        if self.model_provider not in SUPPORTED_MODEL_PROVIDERS:
            errors.append(
                f"MODEL_PROVIDER must be one of {sorted(SUPPORTED_MODEL_PROVIDERS)}, "
                f"got {self.model_provider!r}"
            )
        elif self.model_provider == "openrouter" and not self.openrouter_api_key:
            errors.append("MODEL_PROVIDER=openrouter requires OPENROUTER_API_KEY")
        elif self.model_provider == "openai" and not self.openai_api_key:
            errors.append("MODEL_PROVIDER=openai requires OPENAI_API_KEY")
        elif self.model_provider == "generic_openai_compatible":
            for field in ("model_base_url", "model_api_key", "model_name"):
                if not getattr(self, field):
                    errors.append(
                        f"MODEL_PROVIDER=generic_openai_compatible requires {field.upper()}"
                    )

        if not self.agent_gateway_api_key:
            errors.append("AGENT_GATEWAY_API_KEY must not be empty")
        elif self.is_production and self.agent_gateway_api_key == "change-me":
            errors.append(
                "AGENT_GATEWAY_API_KEY must be replaced from the placeholder in production"
            )

        if self.cua_enabled:
            if self.cua_permission_mode != "bounded":
                errors.append(
                    "CUA_PERMISSION_MODE must be 'bounded' in Phase 1 "
                    f"(got {self.cua_permission_mode!r}); unrestricted mode is not allowed"
                )
            if not self.cua_capability_manifest_path:
                errors.append("CUA_ENABLED=true requires CUA_CAPABILITY_MANIFEST_PATH")
            elif not self.cua_capability_manifest_path.startswith("/"):
                errors.append("CUA_CAPABILITY_MANIFEST_PATH must be an absolute path")

        # Designer-only validation is strictly conditional (Safety note 1):
        # a flag-off gateway validates only the legacy requirements above.
        if self.designer_enabled:
            if not self.designer_credentials_key:
                errors.append(
                    "DESIGNER_ENABLED=true requires DESIGNER_CREDENTIALS_KEY "
                    "(base64 32-byte key; generate with scripts/generate_designer_key.py)"
                )
            else:
                from assistant.designer.credentials import (
                    CredentialKeyMissing,
                    load_key,
                )

                try:
                    load_key(self.designer_credentials_key)
                except CredentialKeyMissing as exc:
                    errors.append(f"DESIGNER_CREDENTIALS_KEY invalid: {exc}")
            if not self.designer_openwebui_base_url.startswith(("http://", "https://")):
                errors.append("DESIGNER_OPENWEBUI_BASE_URL must be an http(s) URL")
            if self.designer_session_ttl_minutes <= 0:
                errors.append("DESIGNER_SESSION_TTL_MINUTES must be > 0")
            if self.designer_login_max_attempts <= 0:
                errors.append("DESIGNER_LOGIN_MAX_ATTEMPTS must be > 0")
            if self.is_production and not self.designer_cookie_secure:
                errors.append(
                    "DESIGNER_ENABLED=true in production requires DESIGNER_COOKIE_SECURE=true "
                    "(loopback HTTP without Secure cookies is dev-only)"
                )

        if errors:
            raise SettingsError("Invalid configuration:\n  - " + "\n  - ".join(errors))
        return self
