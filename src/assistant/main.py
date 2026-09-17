"""FastAPI application entry point for the personal assistant gateway.

Settings are validated at import/app-creation time so an invalid
configuration fails before the process serves any traffic. The lifespan
opens PostgreSQL resources, builds the provider-independent model, loads
filtered CUA tools, and assembles the one Deep Agent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from assistant import __version__
from assistant.agent.build import build_agent
from assistant.api import chat_route, models_route
from assistant.memory.postgres import open_memory_resources
from assistant.models import build_chat_model
from assistant.observability.logging import setup_logging
from assistant.settings import Settings
from assistant.tools.cua import load_cua_tools
from assistant.tools.registry import assemble_tool_inventory


def _skills_root() -> Path:
    """Developer-authored, read-only skill directory shipped with the app."""
    return Path(__file__).resolve().parents[2] / "src" / "assistant" / "skills"


LifespanFn = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(
    settings: Settings | None = None,
    *,
    lifespan: LifespanFn | None = None,
) -> FastAPI:
    """Build the FastAPI application with validated settings."""
    settings = settings or Settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="Personal Assistant Gateway",
        version=__version__,
        # Docs are loopback-only in Phase 1; keep them enabled for local troubleshooting.
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.include_router(models_route.router)
    app.include_router(chat_route.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> Any:
        store = getattr(request.app.state, "store", None)
        if store is None:
            return JSONResponse(
                status_code=503,
                content={"status": "not-ready", "reason": "store not initialized"},
            )
        try:
            await store.asearch(("healthcheck",), limit=1)
        except Exception as exc:  # noqa: BLE001 -- readiness must classify every failure
            return JSONResponse(
                status_code=503,
                content={"status": "not-ready", "reason": type(exc).__name__},
            )
        return {"status": "ready"}

    return app


def _build_lifespan(settings: Settings) -> LifespanFn:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resources_cm = open_memory_resources(settings.database_url)
        resources = await resources_cm.__aenter__()
        app.state.store = resources.store
        app.state.saver = resources.saver

        model = build_chat_model(settings)
        app.state.utility_model = model

        try:
            if settings.cua_enabled:
                connection = await load_cua_tools(settings)
                extra_tools = assemble_tool_inventory(connection.tools)
                app.state.cua_tools_by_name = connection.tools_by_name
            else:
                extra_tools = assemble_tool_inventory([])
                app.state.cua_tools_by_name = {}
            bundle = build_agent(
                model=model,
                checkpointer=resources.saver,
                store=resources.store,
                skills_root=_skills_root(),
                extra_tools=extra_tools,
            )
            app.state.agent = bundle.agent
        except BaseException:
            await resources_cm.__aexit__(None, None, None)
            raise

        try:
            yield
        finally:
            await resources_cm.__aexit__(None, None, None)

    return lifespan


def create_application(settings: Settings | None = None) -> FastAPI:
    """Production application: validated settings + full lifespan wiring."""
    settings = settings or Settings()
    return create_app(settings, lifespan=_build_lifespan(settings))


def main() -> None:
    """Run the gateway with uvicorn using validated settings.

    Uses the factory form so importing this module stays side-effect-free
    (tests and tooling import it without a server environment).
    """
    import uvicorn

    settings = Settings()
    uvicorn.run(
        "assistant.main:create_application",
        factory=True,
        host=settings.app_host,
        port=settings.app_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
