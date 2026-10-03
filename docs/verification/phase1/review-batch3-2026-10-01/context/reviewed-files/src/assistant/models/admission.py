"""Provider request admission at the REAL transport boundary (D11).

Every chat-model request — graph-internal sub-calls, retries, streaming —
passes through :class:`ProviderAdmissionChatModel` (or the admitted
runnable its ``bind_tools``/``bind`` return) BEFORE the inner provider is
invoked. Admission:

- reads the CURRENT invocation scope from a ContextVar set by the mission
  transport (plain chat runs have no scope and pass through un-metered);
- records an ATTEMPTED row durably (unique per-request id) BEFORE the
  provider call, then settles it COMPLETED (with reported token usage when
  the response carries it) or FAILED;
- enforces the invocation ceiling from the mission's OWN documented budget
  limits (never a hardcoded count) and refuses the request BEFORE dispatch
  when the ceiling is reached;
- fails CLOSED: unavailable durable accounting (no store, storage error)
  denies the request instead of silently allowing it.

The outer Deep invocation charge is untouched: this module never touches
the ``deep_calls`` budget, which the service settles exactly once per
invocation. Cost is never guessed — unknown stays unknown.
"""

from __future__ import annotations

import contextvars
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_core.runnables.config import ensure_config

from assistant.missions.contracts import new_id


class ProviderRequestDenied(RuntimeError):
    """The transport refused to dispatch this provider request."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"PROVIDER_REQUEST_DENIED: {reason}")
        self.reason = reason


#: One controller per process: the runtime owns it, the mission store is
#: bound once the service exists, and the CURRENT invocation scope travels
#: through this ContextVar (set outside the graph, read inside model nodes
#: — context flows inward, unlike a value set inside a child task).
class ProviderAdmissionController:
    """Process-wide admission owner: store binding + invocation scope."""

    def __init__(self) -> None:
        self._store: Any = None
        self._scope: contextvars.ContextVar[Any] = contextvars.ContextVar(
            "provider_admission_scope", default=None
        )

    def bind_store(self, store: Any) -> None:
        """Bind the durable mission store (once the service exists)."""
        self._store = store

    def current_scope(self) -> Any:
        return self._scope.get()

    def push_scope(self, scope: Any) -> None:
        """Bind THIS task's invocation scope (context flows inward)."""
        self._scope.set(scope)

    def pop_scope(self, scope: Any) -> None:
        if self.current_scope() is scope:
            self._scope.set(None)

    async def open_scope(self, mission_id: str, plan_version: int,
                         call_key: str) -> Any:
        """Open one invocation scope with the mission's documented limit.

        The ceiling comes from the mission's own budget limits row — not a
        module constant. Opens fail closed: without a bound store (or with
        an unreadable mission) the scope denies every request.
        """
        max_requests: int | None = None
        if self._store is not None:
            try:
                record = await self._store.get_mission(mission_id)
            except Exception:  # noqa: BLE001 -- accounting failure denies
                record = None
            if record is not None:
                max_requests = int(getattr(record.budget_limits,
                                           "max_provider_requests", 16) or 0)
        scope = _InvocationScope(
            controller=self, mission_id=mission_id, plan_version=plan_version,
            call_key=call_key[:200], max_requests=max_requests,
        )
        return scope


    def settle(self, request_id: str, state: str, *,
               input_tokens: int | None = None,
               output_tokens: int | None = None) -> None:
        """Settle one admitted request: COMPLETED or FAILED (best effort —
        the durable ATTEMPTED row already bounds the ceiling honestly)."""
        if self._store is None:
            return
        try:
            self._store.settle_provider_request_sync(
                request_id, state,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
        except Exception:  # noqa: BLE001 -- settlement never masks the outcome
            return


class _InvocationScope:
    """One controller invocation: ceiling + durable mission identity."""

    def __init__(self, *, controller: ProviderAdmissionController,
                 mission_id: str, plan_version: int, call_key: str,
                 max_requests: int | None) -> None:
        self._controller = controller
        self.mission_id = mission_id
        self.plan_version = plan_version
        self.call_key = call_key
        self.max_requests = max_requests
        self.admitted = 0
        self.completed = 0
        self.failed = 0

    def admit(self, request_id: str) -> None:
        """Pre-request admission: durable row, then the ceiling check.

        The durable ATTEMPTED row is written FIRST so an attempted-but-
        denied request is still accounted; the ceiling then refuses BEFORE
        the provider is invoked.
        """
        store = self._controller._store
        if store is None:
            raise ProviderRequestDenied("durable provider accounting is unavailable")
        # The ATTEMPTED row binds mission/version identity durably.
        try:
            store.admit_provider_request_sync(
                request_id,
                mission_id=self.mission_id,
                plan_version=self.plan_version,
                call_key=self.call_key,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderRequestDenied(
                f"provider accounting failed: {exc}"
            ) from exc
        if self.max_requests is None:
            raise ProviderRequestDenied(
                "the mission's provider-request allowance is unknown; "
                "an unbounded invocation is refused"
            )
        self.admitted += 1
        if self.admitted > self.max_requests:
            raise ProviderRequestDenied(
                f"provider request ceiling exceeded for mission "
                f"{self.mission_id} v{self.plan_version}: "
                f"{self.admitted} > {self.max_requests} (invocation scope)"
            )

    def complete(self, request_id: str, response: Any) -> None:
        self.completed += 1
        usage = getattr(response, "usage_metadata", None) or {}
        self._controller.settle(
            request_id, "COMPLETED",
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )

    def fail(self, request_id: str) -> None:
        self.failed += 1
        self._controller.settle(request_id, "FAILED")


def _usage_of(message: Any) -> dict[str, Any]:
    meta = getattr(message, "usage_metadata", None)
    return meta if isinstance(meta, dict) else {}


class _AdmittedRunnable(Runnable):
    """An admitted wrapper around ANY bound runnable (bind_tools results,
    structured-output chains). Scope resolves per call from the ContextVar;
    with no scope (plain chat) the call passes through untouched."""

    def __init__(self, bound: Runnable, controller: ProviderAdmissionController) -> None:
        self._bound = bound
        self._controller = controller

    def bind(self, **kwargs: Any) -> Runnable:  # type: ignore[override]
        return _AdmittedRunnable(self._bound.bind(**kwargs), self._controller)

    def __getattr__(self, item: str) -> Any:
        # Unrecognized surface (get_name, with_config, ...): delegate to the
        # bound runnable.
        return getattr(self._bound, item)

    # -- admitted execution -------------------------------------------------

    def _scope(self) -> Any:
        return self._controller.current_scope()

    def _admitted(self, request_id: str) -> Any:
        scope = self._scope()
        if scope is None:
            return None
        scope.admit(request_id)
        return scope

    async def ainvoke(self, input: Any, config: Any = None,
                      **kwargs: Any) -> Any:
        request_id = new_id()
        scope = self._admitted(request_id)
        try:
            out = await self._bound.ainvoke(input, ensure_config(config), **kwargs)
        except BaseException:
            if scope is not None:
                scope.fail(request_id)
            raise
        if scope is not None:
            scope.complete(request_id, out)
        return out

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        request_id = new_id()
        scope = self._admitted(request_id)
        try:
            out = self._bound.invoke(input, ensure_config(config), **kwargs)
        except BaseException:
            if scope is not None:
                scope.fail(request_id)
            raise
        if scope is not None:
            scope.complete(request_id, out)
        return out

    async def astream(self, input: Any, config: Any = None,
                      **kwargs: Any) -> Any:  # type: ignore[override]
        request_id = new_id()
        scope = self._admitted(request_id)
        try:
            last: Any = None
            async for chunk in self._bound.astream(
                input, ensure_config(config), **kwargs
            ):
                last = chunk
                yield chunk
        except BaseException:
            if scope is not None:
                scope.fail(request_id)
            raise
        if scope is not None:
            scope.complete(request_id, last)

    def stream(self, input: Any, config: Any = None,
               **kwargs: Any) -> Any:  # type: ignore[override]
        request_id = new_id()
        scope = self._admitted(request_id)
        try:
            last: Any = None
            for chunk in self._bound.stream(input, ensure_config(config), **kwargs):
                last = chunk
                yield chunk
        except BaseException:
            if scope is not None:
                scope.fail(request_id)
            raise
        if scope is not None:
            scope.complete(request_id, last)


class ProviderAdmissionChatModel(BaseChatModel):
    """The process chat model: delegates everything to the inner provider
    model and admits every request at THIS boundary first."""

    model_config = {"arbitrary_types_allowed": True}

    inner: Any
    controller: Any

    @property
    def _llm_type(self) -> str:  # type: ignore[override]
        return f"admitted:{getattr(self.inner, '_llm_type', 'chat')}"

    def __getattr__(self, item: str) -> Any:
        # Unrecognized surface: delegate to the inner provider model.
        return getattr(self.__dict__["inner"], item)

    @property
    def _identifying_params(self) -> dict[str, Any]:
        params = getattr(self.inner, "_identifying_params", {})
        return dict(params) if isinstance(params, dict) else {}

    def bind_tools(self, *args: Any, **kwargs: Any) -> Runnable:  # type: ignore[override]
        return _AdmittedRunnable(self.inner.bind_tools(*args, **kwargs), self.controller)

    def bind(self, **kwargs: Any) -> Runnable:  # type: ignore[override]
        return _AdmittedRunnable(self.inner.bind(**kwargs), self.controller)

    def with_structured_output(self, *args: Any,
                               **kwargs: Any) -> Runnable:  # type: ignore[override]
        return _AdmittedRunnable(
            self.inner.with_structured_output(*args, **kwargs), self.controller
        )

    # -- admitted generation ------------------------------------------------

    def _scope(self) -> Any:
        return self.controller.current_scope()

    def _run_admitted(self, request_id: str) -> Any:
        scope = self._scope()
        if scope is None:
            return None
        scope.admit(request_id)
        return scope

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        request_id = new_id()
        scope = self._run_admitted(request_id)
        try:
            out = self.inner._generate(messages, stop=stop,
                                       run_manager=run_manager, **kwargs)
        except BaseException:
            if scope is not None:
                scope.fail(request_id)
            raise
        if scope is not None:
            scope.complete(request_id, out)
        return out

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        request_id = new_id()
        scope = self._run_admitted(request_id)
        try:
            out = await self.inner._agenerate(messages, stop=stop,
                                              run_manager=run_manager, **kwargs)
        except BaseException:
            if scope is not None:
                scope.fail(request_id)
            raise
        if scope is not None:
            scope.complete(request_id, out)
        return out

    def _stream(self, messages: list[BaseMessage], stop: list[str] | None = None,
                run_manager: Any = None,
                **kwargs: Any) -> Any:  # type: ignore[override]
        request_id = new_id()
        scope = self._run_admitted(request_id)
        try:
            last: Any = None
            for chunk in self.inner._stream(messages, stop=stop,
                                            run_manager=run_manager, **kwargs):
                last = chunk
                yield chunk
        except BaseException:
            if scope is not None:
                scope.fail(request_id)
            raise
        if scope is not None:
            scope.complete(request_id, last)

    async def _astream(self, messages: list[BaseMessage], stop: list[str] | None = None,
                       run_manager: Any = None,
                       **kwargs: Any) -> Any:  # type: ignore[override]
        request_id = new_id()
        scope = self._run_admitted(request_id)
        try:
            last: Any = None
            async for chunk in self.inner._astream(messages, stop=stop,
                                                   run_manager=run_manager, **kwargs):
                last = chunk
                yield chunk
        except BaseException:
            if scope is not None:
                scope.fail(request_id)
            raise
        if scope is not None:
            scope.complete(request_id, last)


_CONTROLLER = ProviderAdmissionController()


def get_admission_controller() -> ProviderAdmissionController:
    """The process-wide admission controller (one chat model, one store)."""
    return _CONTROLLER


def wrap_chat_model(model: BaseChatModel,
                    controller: ProviderAdmissionController) -> ProviderAdmissionChatModel:
    """The ONE place the process chat model gains transport admission."""
    return ProviderAdmissionChatModel(inner=model, controller=controller)


__all__ = [
    "ProviderAdmissionChatModel",
    "ProviderAdmissionController",
    "ProviderRequestDenied",
    "wrap_chat_model",
]
