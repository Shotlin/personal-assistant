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

    # Sani local persistence (Sani master doc sections 2-5). "postgres" is
    # the legacy gateway backend and the default; "sqlite" swaps the Deep
    # Agent checkpointer + long-term memory to the embedded sani.db under
    # sani_data_dir -- no server, no Docker (policy stays backend-agnostic).
    memory_backend: str = "postgres"
    sani_data_dir: str = ""

    # CUA (computer use)
    cua_enabled: bool = True
    cua_command: str = "cua-driver"
    # A packaged desktop host starts the driver itself in embedded mode and
    # supplies a private Unix socket.  An empty value retains the standalone
    # CLI integration used by development and verification scripts.
    cua_socket: str = ""
    cua_permission_mode: str = "bounded"
    cua_capability_manifest_path: str = ""
    cua_existing_profile_grant: bool = False
    cua_artifact_dir: str = "var/artifacts"
    # The packaged sani-core process is not the macOS GUI host.  The native
    # Sani app probes the TCC grants made to *its* identity and passes those
    # read-only results to its embedded child at launch.
    sani_host_accessibility_permission: str = "unknown"
    sani_host_screen_recording_permission: str = "unknown"

    # Open WebUI integration

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

    # Velo controller (master plan sections 4-5): JEV is the structured
    # decision service for interpretation; provider code stays behind one
    # interface and fails closed -- an unavailable JEV is reported, never
    # silently substituted with another model.
    velo_jev_enabled: bool = False
    velo_provider: str = "openrouter"
    typesafe_api_key: str = ""
    velo_jev_model: str = "jev-latest"
    # Only a System One-compatible root (Noul/Choice over /v1/systemone).
    velo_typesafe_base_url: str = ""
    velo_max_decision_seconds: int = 30
    # An ordinary local command is one action, not a 13-minute campaign:
    # Route A/B tasks carry their own, much shorter deadline.
    velo_command_deadline_seconds: int = 90

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def sani_db_path(self) -> str:
        """Path of the embedded Sani database (used by the sqlite backend)."""
        from pathlib import Path

        return str(Path(self.sani_data_dir).expanduser() / "sani.db")

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

        if self.memory_backend not in {"postgres", "sqlite"}:
            errors.append(
                f"MEMORY_BACKEND must be 'postgres' or 'sqlite', got {self.memory_backend!r}"
            )
        elif self.memory_backend == "sqlite" and not self.sani_data_dir:
            errors.append(
                "MEMORY_BACKEND=sqlite requires SANI_DATA_DIR (the Sani "
                "application-data directory that holds sani.db)"
            )
        if self.status_quiet_seconds <= 0:
            errors.append(f"STATUS_QUIET_SECONDS must be > 0, got {self.status_quiet_seconds}")

        if self.velo_jev_enabled:
            if self.velo_provider not in {"openrouter", "typesafe"}:
                errors.append(
                    f"VELO_PROVIDER must be 'openrouter' or 'typesafe', "
                    f"got {self.velo_provider!r}"
                )
            elif self.velo_provider == "openrouter" and not self.openrouter_api_key:
                errors.append("VELO_PROVIDER=openrouter requires OPENROUTER_API_KEY")
            elif self.velo_provider == "typesafe" and not self.typesafe_api_key:
                errors.append("VELO_PROVIDER=typesafe requires TYPESAFE_API_KEY")
            if self.velo_typesafe_base_url and not self.velo_typesafe_base_url.startswith(
                "https://"
            ):
                errors.append(
                    f"VELO_TYPESAFE_BASE_URL must be an https:// URL (got "
                    f"{self.velo_typesafe_base_url!r})"
                )
            if self.velo_max_decision_seconds <= 0:
                errors.append(
                    f"VELO_MAX_DECISION_SECONDS must be > 0, got {self.velo_max_decision_seconds}"
                )
        if self.velo_command_deadline_seconds <= 0:
            errors.append(
                f"VELO_COMMAND_DEADLINE_SECONDS must be > 0, got "
                f"{self.velo_command_deadline_seconds}"
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
            if self.cua_permission_mode not in ("bounded", "standard"):
                errors.append(
                    "CUA_PERMISSION_MODE must be 'bounded' or 'standard' "
                    f"(got {self.cua_permission_mode!r}); unrestricted mode is not allowed"
                )
            if self.cua_permission_mode == "bounded" and not self.cua_capability_manifest_path:
                errors.append(
                    "CUA_ENABLED=true with bounded mode requires CUA_CAPABILITY_MANIFEST_PATH"
                )
            if self.cua_capability_manifest_path and not (
                self.cua_capability_manifest_path.startswith("/")
            ):
                errors.append("CUA_CAPABILITY_MANIFEST_PATH must be an absolute path")
            if self.cua_socket and not self.cua_socket.startswith("/"):
                errors.append("CUA_SOCKET must be an absolute path when configured")

        if errors:
            raise SettingsError("Invalid configuration:\n  - " + "\n  - ".join(errors))
        return self
