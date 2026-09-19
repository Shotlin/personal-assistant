"""Runtime compiler (P5 skeleton) + scope refusal (Fix 3).

The full compiler arrives with P5; the scope-refusal contract is
security-critical and lands here first so P4 can pin it:

- a runtime compiled for a SHARED scope must not include USER_SCOPED
  connectors (another user's credential must never serve someone else);
- RUN_SCOPED connectors are accepted in any scope but force per-run
  leases downstream (P5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from assistant.designer.connectors import ScopeKind
from assistant.designer.errors import DesignerError


@dataclass(frozen=True)
class RuntimeScopeRequest:
    """The scope a runtime is being compiled for + the connectors it
    would bind. Compilation refuses structurally unsafe combinations."""

    runtime_scope: ScopeKind
    connectors: dict[str, ScopeKind] = field(default_factory=dict)


def refuse_mismatched_scope(request: RuntimeScopeRequest) -> None:
    """Raise permission_denied when a user-scoped connector would land in
    a shared runtime — a guess is never made (Fix 3)."""
    if request.runtime_scope is not ScopeKind.SHARED:
        return
    offenders = sorted(
        name for name, scope in request.connectors.items()
        if scope is ScopeKind.USER_SCOPED
    )
    if offenders:
        raise DesignerError(
            "permission_denied",
            f"user-scoped connector(s) {offenders} cannot join a shared runtime; "
            "compile a user-scoped runtime instead",
        )
