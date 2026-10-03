"""Mission evidence: sanitize before any sink; trusted verifiers only.

Two rules from file 03 §10 are enforced structurally here:

- **Sanitize before disk/DB/log/model.** A candidate carrying secret-shaped
  content is never written: the store records a WITHHELD reference (no
  content locator) so the mission trace stays honest about what was seen
  and dropped. Regex screening is a text gate, not a pixel gate — images
  are withheld by default in Phase 1 and only a sanitized, explicitly
  allowed capture may pass.
- **The executor cannot manufacture acceptance.** :meth:`EvidenceStore.verify`
  answers a :class:`CheckSpec` only through the trusted verifier catalog;
  a check naming an unknown verifier, or evidence outside the required
  freshness window, fails closed.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from assistant.memory.policy import contains_secret
from assistant.missions.contracts import (
    CheckResult,
    CheckSpec,
    EvidenceCandidate,
    EvidenceRef,
    new_id,
    text_digest,
)
from assistant.missions.store import MissionStore
from assistant.observability.logging import redact

#: Verifier ids this process trusts. Anything else fails a check closed.
VERIFIER_CATALOG: dict[str, str] = {
    "page_state": "1.0.0",
    "url_origin": "1.0.0",
    "artifact_readable": "1.0.0",
    "payload_in_document": "1.0.0",
    "action_absent": "1.0.0",
    "app_foreground": "1.0.0",
    # C05/N07: recipe-appropriate independent checks for the typed/scroll
    # fast paths. Both are deterministic and zero-model; field_value
    # compares a field's observed value against the payload the executor
    # resolved from the vault (the check spec carries only the REF, never
    # the text itself).
    "field_value": "1.0.0",
    "window_visible": "1.0.0",
    # D06: scroll and ordinal activation owe EXACT before/after evidence —
    # a re-observed window alone (window_visible {}) proves nothing. Both
    # verifiers require a before→after pair bound to ONE window; when the
    # driver reports no comparable state they fail honestly.
    "scroll_effect": "1.0.0",
    "press_effect": "1.0.0",
}

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})


def _evidence_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


#: D07: ordinary evidence expires by default at creation time (seven days).
#: Retention is then a normal lifecycle operation, not a manual SQL sweep.
DEFAULT_EVIDENCE_TTL_MS = 7 * 24 * 60 * 60 * 1000


class EvidenceStore:
    """Owns sanitized evidence files and the trusted check evaluations."""

    def __init__(
        self,
        root: Path | str,
        store: MissionStore | None = None,
        *,
        trusted_roots: list[Path | str] | None = None,
        ttl_ms: int = DEFAULT_EVIDENCE_TTL_MS,
    ) -> None:
        self._root = Path(root).resolve()
        self._store = store
        # R09/F11: the verifier's allowed roots come from the TRUSTED
        # catalog configuration, never from model-supplied check payloads.
        self._trusted_roots = [
            Path(r).expanduser().resolve() for r in (trusted_roots or [])
        ]
        self._ttl_ms = max(1, int(ttl_ms))

    @property
    def root(self) -> Path:
        return self._root

    # -- put ---------------------------------------------------------------------

    async def put(
        self,
        candidate: EvidenceCandidate,
        *,
        mission_id: str,
        execution_id: str | None = None,
        allow_image: bool = False,
    ) -> EvidenceRef:
        """Sanitize, then persist. Withheld evidence records the fact only."""
        now = candidate.captured_at_ms or int(time.time() * 1000)
        evidence_id = new_id()
        if candidate.kind in {"screenshot", "image"} and not allow_image:
            return await self._withheld_ref(
                evidence_id, mission_id, execution_id, candidate.kind, now,
                "images are withheld by default in mission mode",
            )
        text = _evidence_text(candidate.payload)
        if contains_secret(text) is not None or redact(text) != text:
            return await self._withheld_ref(
                evidence_id, mission_id, execution_id, candidate.kind, now,
                "payload screened as secret-bearing; content not stored",
            )
        relative = Path(mission_id) / f"{evidence_id}.json"
        target = self._root / relative
        rendered = json.dumps(
            {"kind": candidate.kind, "captured_at_ms": now, "payload": candidate.payload},
            ensure_ascii=False,
            sort_keys=True,
            indent=1,
        )
        sha256 = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
        except OSError:
            # Evidence unavailable is a mission-blocking fact, not a crash:
            # record the withheld ref and let the acceptance gate refuse.
            return await self._withheld_ref(
                evidence_id, mission_id, execution_id, candidate.kind, now,
                "evidence storage unavailable; content not stored",
            )
        ref = EvidenceRef(
            evidence_id=evidence_id,
            mission_id=mission_id,
            execution_id=execution_id,
            kind=candidate.kind,
            relative_path=str(relative),
            sha256=sha256,
            sensitivity="INTERNAL",
            captured_at_ms=now,
            # D07: expiry is configured during ordinary creation, so the
            # retention sweep has an honest bound for every live row.
            expires_at_ms=now + self._ttl_ms,
        )
        if self._store is not None:
            await self._store.put_evidence(ref)
        return ref

    async def _withheld_ref(
        self,
        evidence_id: str,
        mission_id: str,
        execution_id: str | None,
        kind: str,
        now: int,
        reason: str,
    ) -> EvidenceRef:
        ref = EvidenceRef(
            evidence_id=evidence_id,
            mission_id=mission_id,
            execution_id=execution_id,
            kind=kind,
            sha256=text_digest(reason),
            sensitivity="WITHHELD",
            redaction_status="WITHHELD",
            captured_at_ms=now,
        )
        if self._store is not None:
            await self._store.put_evidence(ref)
        return ref

    # -- retention sweep (C07) ------------------------------------------------------

    def sweep_deleted_files(self, deleted_paths: list[str]) -> int:
        """Unlink evidence files whose rows the retention sweep deleted.

        C07: only paths the store's tombstoned rows named are removed —
        this never deletes a file whose row is still live (holds included).
        """
        removed = 0
        for relative in deleted_paths:
            if not relative:
                continue
            target = (self._root / relative).resolve()
            if not _contained(target, self._root) or target.is_symlink():
                continue
            try:
                target.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        return removed

    def remove_orphan_files(self, live_relative_paths: set[str]) -> int:
        """Delete artifact files with no live evidence row (orphan cleanup)."""
        removed = 0
        if not self._root.is_dir():
            return 0
        for path in sorted(self._root.rglob("*.json")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = str(path.relative_to(self._root))
            if relative in live_relative_paths:
                continue
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        return removed

    async def live_relative_paths(self) -> set[str]:
        if self._store is None:
            return set()
        refs = await self._store.get_all_evidence_paths()
        return {r for r in refs if r}

    # -- read --------------------------------------------------------------------

    def load(self, ref: EvidenceRef) -> dict[str, Any] | None:
        """Read back a SAFE evidence payload; WITHHELD refs have none.

        R09/F11 (RP09): the stored content hash is VERIFIED against the
        ref, and ownership/expiry are enforced -- a modified, mis-owned, or
        expired file resolves to nothing.
        """
        if ref.redaction_status == "WITHHELD" or not ref.relative_path:
            return None
        target = (self._root / ref.relative_path).resolve()
        if not _contained(target, self._root):
            return None
        try:
            raw = target.read_bytes()
        except OSError:
            return None
        if hashlib.sha256(raw).hexdigest() != ref.sha256:
            return None  # tampered content is not loadable evidence
        if ref.expires_at_ms is not None and ref.expires_at_ms <= int(time.time() * 1000):
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    # -- trusted checks ------------------------------------------------------------

    async def verify(
        self,
        check: CheckSpec,
        item: Any,
        *,
        refs: list[EvidenceRef] | None = None,
        not_before_ms: int = 0,
    ) -> CheckResult:
        """One check, one honest answer, from the trusted catalog only.

        ``not_before_ms`` is the structural pre/post-action bound: evidence
        captured before the unit was dispatched can never verify its effect
        (RF-03 -- text that merely existed before the action proves nothing).
        """
        now = int(time.time() * 1000)
        version = VERIFIER_CATALOG.get(check.verifier_id)
        if version is None:
            return _failed(check, now, f"verifier {check.verifier_id!r} is not trusted")
        refs = refs or []
        fresh = [
            ref
            for ref in refs
            if now - ref.captured_at_ms <= max(self._max_age(item), 0)
            and ref.captured_at_ms >= not_before_ms
        ]
        if refs and not fresh:
            return _failed(
                check, now,
                "all candidate evidence is stale: captured before the "
                "action or outside the freshness window",
            )
        payload_by_id = {ref.evidence_id: (self.load(ref) or {}).get("payload") for ref in fresh}
        # R09/F11: a check only runs against evidence owned by THIS
        # mission, matching the packet's scope hash.
        owner_id = getattr(item, "mission_id", None)
        if owner_id is not None:
            for ref in fresh:
                if ref.mission_id != owner_id:
                    return _failed(check, now, "evidence belongs to another mission")
        expected_scope = getattr(item, "expected_scope", None)
        check_scope = getattr(check, "target_scope_hash", "")
        if check_scope and expected_scope is not None:
            if check_scope != getattr(expected_scope, "scope_hash", check_scope):
                return _failed(check, now, "check scope hash does not match the packet scope")
        try:
            passed, reason, evidence_ids = self._evaluate(check, fresh, payload_by_id)
        except _VerifierFailure as exc:
            passed, reason, evidence_ids = False, str(exc), []
        return CheckResult(
            check_id=check.check_id,
            passed=passed,
            evidence_ids=evidence_ids,
            checked_at_ms=now,
            verifier_id=check.verifier_id,
            verifier_version=version,
            reason=reason[:1000],
        )

    def _max_age(self, item: Any) -> int:
        requirements = getattr(item, "evidence_requirements", None)
        if requirements is None:
            return 60_000
        return int(getattr(requirements, "max_age_ms", 60_000) or 60_000)

    def _evaluate(
        self,
        check: CheckSpec,
        refs: list[EvidenceRef],
        payload_by_id: dict[str, Any],
    ) -> tuple[bool, str, list[str]]:
        expected = check.expected
        if check.verifier_id == "page_state":
            markers = [str(m).lower() for m in expected.get("markers", [])]
            if not markers:
                raise _VerifierFailure("page_state requires expected markers")
            for ref in refs:
                payload = payload_by_id.get(ref.evidence_id) or {}
                observed_payload = {k: v for k, v in payload.items() if k != "resolved_payloads"}
                haystack = _evidence_text(observed_payload).lower()
                if all(marker in haystack for marker in markers):
                    return True, "all markers observed", [ref.evidence_id]
            return False, "not all expected markers were observed in the evidence", []
        if check.verifier_id == "url_origin":
            wanted = str(expected.get("origin", "")).strip().lower()
            if not wanted:
                raise _VerifierFailure("url_origin requires an expected origin")
            for ref in refs:
                payload = payload_by_id.get(ref.evidence_id) or {}
                observed = str(payload.get("origin", "")).strip().lower()
                if observed and observed == wanted:
                    return True, "origin matches exactly", [ref.evidence_id]
            return False, "no evidence reports exactly the expected origin", []
        if check.verifier_id == "app_foreground":
            wanted = str(expected.get("app", "")).strip().lower()
            if not wanted:
                raise _VerifierFailure("app_foreground requires an expected app identity")
            for ref in refs:
                payload = payload_by_id.get(ref.evidence_id) or {}
                for app in payload.get("apps", []):
                    identity = f"{app.get('bundle_id', '')} {app.get('name', '')}".lower()
                    if wanted in identity and app.get("running") and app.get("active"):
                        return True, "the requested app is running in the foreground", [
                            ref.evidence_id
                        ]
            return False, "the requested app is not running in the foreground", []
        if check.verifier_id == "field_value":
            ref_id = str(expected.get("payload_ref", "")).strip()
            if not ref_id:
                raise _VerifierFailure("field_value requires a payload_ref")
            for ref in refs:
                payload = payload_by_id.get(ref.evidence_id) or {}
                resolved = (payload.get("resolved_payloads") or {}).get(ref_id)
                if resolved is None:
                    continue
                # D06: when the executor captured BEFORE evidence, the typed
                # field is the field that held FOCUS before the action — the
                # same value in a different field is not success.
                focused_token = _focused_token_from(refs, payload_by_id)
                for element in payload.get("elements", []):
                    if not isinstance(element, dict):
                        continue
                    if (
                        focused_token is not None
                        and str(element.get("element_token") or "") != focused_token
                    ):
                        continue
                    value = str(element.get("value") or "")
                    if value and value == str(resolved):
                        return True, "the resolved payload is in the focused field", [
                            ref.evidence_id
                        ]
            return False, "no observed field carries the resolved payload", []
        if check.verifier_id == "scroll_effect":
            direction = str(expected.get("direction", "")).strip().lower()
            min_delta = int(expected.get("min_delta", 1) or 1)
            if direction not in {"up", "down", "left", "right"}:
                raise _VerifierFailure("scroll_effect requires a scroll direction")
            ordered = sorted(refs, key=lambda r: r.captured_at_ms)
            if len(ordered) < 2:
                return False, "scroll needs before and after observations", []
            pairs = [(r, payload_by_id.get(r.evidence_id) or {}) for r in ordered]
            offsets: list[float] = []
            for _ref, payload in pairs:
                offset = payload.get("scroll_offset")
                if not isinstance(offset, (int, float)) or isinstance(offset, bool):
                    return False, (
                        "the driver did not report an independent scroll offset; "
                        "the check cannot be evaluated honestly"
                    ), []
                offsets.append(float(offset))
            windows = {(p.get("pid"), p.get("window_id")) for _, p in pairs}
            if len(windows) != 1 or (None, None) in windows:
                return False, "before/after observations are not bound to one window", []
            delta = (
                offsets[-1] - offsets[0]
                if direction in {"down", "right"}
                else offsets[0] - offsets[-1]
            )
            if delta >= min_delta:
                return True, f"scroll offset moved {delta:+.0f}", [ordered[-1].evidence_id]
            return False, (
                f"scroll offset moved {delta:+.0f}; {min_delta} required for {direction}"
            ), []
        if check.verifier_id == "press_effect":
            ordered = sorted(refs, key=lambda r: r.captured_at_ms)
            if len(ordered) < 2:
                return False, "ordinal activation needs before and after observations", []
            pairs = [(r, payload_by_id.get(r.evidence_id) or {}) for r in ordered]
            windows = {(p.get("pid"), p.get("window_id")) for _, p in pairs}
            if len(windows) != 1 or (None, None) in windows:
                return False, "before/after observations are not bound to one window", []
            before = {
                str(e.get("element_token")): e
                for e in pairs[0][1].get("elements", [])
                if isinstance(e, dict) and e.get("element_token")
            }
            for element in pairs[-1][1].get("elements", []):
                if not isinstance(element, dict) or not element.get("element_token"):
                    continue
                prior = before.get(str(element.get("element_token")))
                if prior is None:
                    continue
                for flag in ("focused", "checked", "selected", "expanded", "pressed"):
                    if bool(element.get(flag)) and not bool(prior.get(flag)):
                        return True, (
                            f"control {element.get('element_token')!r} gained {flag}"
                        ), [ordered[-1].evidence_id]
            return False, "no control's activation state changed after the press", []
        if check.verifier_id == "window_visible":
            wanted = str(expected.get("app", "")).strip().lower()
            for ref in refs:
                payload = payload_by_id.get(ref.evidence_id) or {}
                apps = payload.get("apps", [])
                if wanted:
                    match = any(
                        wanted in f"{a.get('bundle_id', '')} {a.get('name', '')}".lower()
                        for a in apps
                        if isinstance(a, dict)
                    )
                    if match:
                        return True, "the scoped window was re-observed after the action", [
                            ref.evidence_id
                        ]
                elif payload.get("window_id") is not None:
                    return True, "the scoped window was re-observed after the action", [
                        ref.evidence_id
                    ]
            return False, "no fresh observation of the scoped window exists", []
        if check.verifier_id == "artifact_readable":
            return self._verify_artifact(expected)
        if check.verifier_id == "payload_in_document":
            expected_payload: Any = expected.get("payload")
            if expected_payload is None:
                raise _VerifierFailure("payload_in_document requires expected payload content")
            for ref in refs:
                payload = payload_by_id.get(ref.evidence_id) or {}
                if payload.get("payload") == expected_payload:
                    return True, "payload matches the approved content", [ref.evidence_id]
            return False, "no evidence carries exactly the expected payload", []
        if check.verifier_id == "action_absent":
            digest = str(expected.get("action_digest", ""))
            if not digest:
                raise _VerifierFailure("action_absent requires an action digest")
            for ref in refs:
                payload = payload_by_id.get(ref.evidence_id) or {}
                performed = payload.get("performed_digests", [])
                if digest in performed:
                    return False, (
                        "the action digest appears in the performed set"
                    ), [ref.evidence_id]
                return True, "the action digest is absent from the performed set", [ref.evidence_id]
        raise _VerifierFailure(f"verifier {check.verifier_id} has no evaluation rule")

    def _verify_artifact(self, expected: dict[str, Any]) -> tuple[bool, str, list[str]]:
        raw_path = str(expected.get("path", ""))
        if not raw_path:
            raise _VerifierFailure("artifact_readable requires a path")
        path = Path(raw_path).expanduser()
        resolved = path.resolve()
        # Roots come from the store configuration, not the check payload.
        allowed_roots = [*self._trusted_roots, self._root]
        if not any(_contained(resolved, root) for root in allowed_roots):
            return False, "artifact path escapes the allowed roots (traversal refused)", []
        if resolved.is_symlink():
            return False, "artifact path traverses a symlink", []
        if not resolved.is_file():
            return False, "artifact does not exist", []
        size = resolved.stat().st_size
        if size == 0:
            return False, "artifact is empty", []
        suffix = resolved.suffix.lower()
        wanted_types = [str(t).lower() for t in expected.get("types", [])]
        if wanted_types and suffix not in wanted_types:
            return False, f"artifact type {suffix or '(none)'} is not an accepted kind", []
        # R09/F11 (TC-24): verify real content, not the suffix alone.
        content_failures = _content_type_mismatch(resolved)
        if content_failures:
            return False, content_failures, []
        wanted_hash = str(expected.get("sha256", "")).lower()
        if wanted_hash:
            actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
            if actual != wanted_hash:
                return False, "artifact hash does not match", []
        return True, f"artifact readable ({size} bytes)", []


class _VerifierFailure(RuntimeError):
    """A check could not be evaluated; the honest answer is a failure."""


def _focused_token_from(refs: list[EvidenceRef], payload_by_id: dict[str, Any]) -> str | None:
    """The element token that held focus in the earliest observation (D06).

    The executor's before-evidence records which editable field was focused
    when the unit started; a typed payload must land in THAT field. When no
    before-evidence exists the binding is unavailable and None is returned.
    """
    ordered = sorted(refs, key=lambda r: r.captured_at_ms)
    if not ordered:
        return None
    payload = payload_by_id.get(ordered[0].evidence_id) or {}
    for element in payload.get("elements", []):
        if isinstance(element, dict) and element.get("focused") and element.get("element_token"):
            return str(element["element_token"])
    return None


def _failed(check: CheckSpec, now: int, reason: str) -> CheckResult:
    return CheckResult(
        check_id=check.check_id,
        passed=False,
        evidence_ids=[],
        checked_at_ms=now,
        verifier_id=check.verifier_id,
        verifier_version=VERIFIER_CATALOG.get(check.verifier_id, "unknown"),
        reason=reason,
    )


#: Magic-byte sniffing for accepted artifact types (TC-24: a renamed or
#: corrupt file is not the artifact it claims to be).
_MAGIC: dict[str, bytes] = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".gif": b"GIF8",
    ".pdf": b"%PDF",
}


def _content_type_mismatch(path: Path) -> str:
    suffix = path.suffix.lower()
    magic = _MAGIC.get(suffix)
    if magic is None:
        return ""
    head = path.read_bytes()[: len(magic)]
    if not head.startswith(magic):
        return f"artifact content does not match its {suffix} type"
    return ""


def _contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def sanitize_text(text: str) -> str:
    """One pass of secret redaction for model-facing or stored text."""
    return redact(text)


_SECRET_SHAPE_RE = re.compile(r"(sk|xoxb|ghp)-[A-Za-z0-9_-]{8,}")


def looks_secret_shaped(text: str) -> bool:
    return bool(_SECRET_SHAPE_RE.search(text)) or contains_secret(text) is not None


__all__ = [
    "EvidenceStore",
    "VERIFIER_CATALOG",
    "looks_secret_shaped",
    "sanitize_text",
]
