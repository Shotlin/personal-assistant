"""FastAPI application entry point for the personal assistant gateway.

Settings are validated at import/app-creation time so an invalid
configuration fails before the process serves any traffic. The lifespan
opens PostgreSQL resources, builds the provider-independent model, loads
filtered CUA tools, and assembles the one Deep Agent.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from assistant import __version__
from assistant.agent.build import build_agent
from assistant.api import chat_route, models_route, run_events_route
from assistant.memory.postgres import open_memory_resources
from assistant.models import build_chat_model
from assistant.observability.logging import setup_logging
from assistant.runtime.runs import RunStore
from assistant.runtime.session import (
    DesktopSessionConfig,
    DesktopSessionManager,
    McpToolDesktopDriver,
)
from assistant.settings import Settings
from assistant.tools.cua import open_cua_connection
from assistant.tools.registry import assemble_tool_inventory

logger = logging.getLogger("assistant.main")

def _null_cua_connection() -> AbstractAsyncContextManager[dict[str, Any]]:
    @asynccontextmanager
    async def cm() -> AsyncIterator[dict[str, Any]]:
        yield {}

    return cm()


#: Interval for the driver keepalive ping (seconds). The daemon's manifest
#: idles sessions out after 30m; a cheap read-only ping every 5 minutes
#: keeps the daemon process and the persistent transport's session alive
#: so a run never starts against a dead session (live 2026-09-18).
KEEPALIVE_INTERVAL_SECONDS = 300


async def _driver_keepalive(connection: Any) -> None:
    """Periodic read-only ping over the persistent MCP session.

    Never touches the model, never mutates anything: one bounded
    observation call per interval. Stops quietly on shutdown.
    """
    tools_by_name = dict(getattr(connection, "tools_by_name", {}) or {})
    ping = tools_by_name.get("get_screen_size")
    if ping is None:
        return
    while True:
        await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)
        try:
            await asyncio.wait_for(ping.ainvoke({}), timeout=30)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- keepalive must never break the gateway
            logger.warning(
                "cua_keepalive_ping_failed", extra={"event": "cua_keepalive_ping_failed"}
            )


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
    app.include_router(run_events_route.router)

    # Designer mounts ONLY when explicitly enabled (C2 + Safety note 1).
    # Flag-off: no designer routes, no designer state, legacy behavior.
    if settings.designer_enabled:
        from fastapi.responses import RedirectResponse

        from assistant.designer.routes import router as designer_router

        app.include_router(designer_router)
        from assistant.designer.routes import install_error_handler, mount_designer_spa

        install_error_handler(app)
        mount_designer_spa(app)

        @app.get("/", include_in_schema=False)
        def root_redirect() -> RedirectResponse:
            return RedirectResponse(url="/designer/")

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


async def _cancel_keepalive(task: Any) -> None:
    """Stop the keepalive task on shutdown without surfacing cancellation."""
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


def _build_lifespan(settings: Settings) -> LifespanFn:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Register each cleanup before the next fallible startup operation.
        # The same stack covers startup failure and ordinary shutdown.
        async with AsyncExitStack() as stack:
            resources = await stack.enter_async_context(
                open_memory_resources(settings.database_url)
            )
            app.state.store = resources.store
            app.state.saver = resources.saver
            run_store = await RunStore.connect(settings.database_url)
            stack.push_async_callback(run_store.close)
            await run_store.setup()
            app.state.run_store = run_store

            model = build_chat_model(settings)
            app.state.utility_model = model
            connection: Any = await stack.enter_async_context(
                open_cua_connection(settings)
                if settings.cua_enabled
                else _null_cua_connection()
            )
            extra_tools = assemble_tool_inventory(list(getattr(connection, "tools", [])))
            app.state.cua_tools_by_name = dict(getattr(connection, "tools_by_name", {}))
            # Keepalive: one read-only ping per interval so the daemon and
            # its transport session never idle out between user turns.
            keepalive_task = asyncio.create_task(_driver_keepalive(connection))
            stack.push_async_callback(_cancel_keepalive, keepalive_task)
            # One trusted DesktopSessionManager over the persistent
            # connection's lifecycle tools (WP3): lazy run-scoped sessions,
            # controller-owned cursor motion, single desktop lease. The
            # manager never recreates the transport.
            lifecycle = dict(getattr(connection, "lifecycle_tools_by_name", {}) or {})
            app.state.desktop_sessions = DesktopSessionManager(
                McpToolDesktopDriver(lifecycle),
                config=DesktopSessionConfig(),
                enabled=settings.active_cursor_persistence_enabled,
            )
            stack.push_async_callback(app.state.desktop_sessions.close_all)
            bundle = build_agent(
                model=model,
                checkpointer=resources.saver,
                store=resources.store,
                skills_root=_skills_root(),
                extra_tools=extra_tools,
            )
            app.state.agent = bundle.agent

            # Designer services initialize ONLY when enabled (Safety note 1).
            # Runs after the legacy app is fully up so Designer startup
            # problems never prevent the Phase-1 gateway from serving.
            if settings.designer_enabled:
                from assistant.designer.service import build_designer_state

                app.state.designer = await build_designer_state(settings, app)
                stack.push_async_callback(app.state.designer["store"].close)
                logger.info(
                    "designer_services_initialized",
                    extra={"event": "designer_services_initialized"},
                )
            yield

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
