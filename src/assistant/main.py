"""FastAPI application entry point for the personal assistant gateway.

Settings are validated at import/app-creation time so an invalid
configuration fails before the process serves any traffic.
"""

from __future__ import annotations

from fastapi import FastAPI

from assistant import __version__
from assistant.observability.logging import setup_logging
from assistant.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with validated settings."""
    settings = settings or Settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="Personal Assistant Gateway",
        version=__version__,
        # Docs are loopback-only in Phase 1; keep them enabled for local troubleshooting.
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def main() -> None:
    """Run the gateway with uvicorn using validated settings."""
    import uvicorn

    settings = Settings()
    uvicorn.run(
        "assistant.main:app",
        host=settings.app_host,
        port=settings.app_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
