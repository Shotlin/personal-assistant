"""Write-only encrypted credentials (R09, Safety note 2).

Design invariants:

- Plaintext secrets enter exactly once (``store``) and are never returned
  by any read path. Callers keep only :class:`CredentialRef` values.
- AESGCM with a fresh random 96-bit nonce per encryption; uniqueness for a
  key enforced by a unique DB index. The AAD binds owner/credential/
  generation so ciphertexts cannot be replayed across owners or versions.
- The encryption key comes from ``DESIGNER_CREDENTIALS_KEY`` (base64, 32
  bytes) and is loaded outside the database. It is fail-fast **only when
  the Designer is enabled** or an explicit Designer command requires it
  (Safety note 1): the flag-off gateway never touches this module.
- Rotation bumps ``generation`` and never exposes old or new secrets;
  revocation is immediate and dispatch paths check it locally.
"""

from __future__ import annotations

import base64
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12  # 96-bit nonce per AESGCM contract
_KEY_BYTES = 32
_AAD_VERSION = b"designer-credential-v1"


class CredentialKeyMissing(RuntimeError):
    """Raised when DESIGNER_CREDENTIALS_KEY is absent/invalid where required."""


def load_key(raw_key: str) -> bytes:
    """Decode the base64 32-byte key; raise on any deviation."""
    if not raw_key:
        raise CredentialKeyMissing("DESIGNER_CREDENTIALS_KEY is not set")
    try:
        key = base64.b64decode(raw_key, validate=True)
    except Exception as exc:
        raise CredentialKeyMissing("DESIGNER_CREDENTIALS_KEY must be valid base64") from exc
    if len(key) != _KEY_BYTES:
        raise CredentialKeyMissing(
            f"DESIGNER_CREDENTIALS_KEY must decode to {_KEY_BYTES} bytes, got {len(key)}"
        )
    return key


def generate_key() -> str:
    """Operator helper: a fresh base64 key for DESIGNER_CREDENTIALS_KEY."""
    return base64.b64encode(os.urandom(_KEY_BYTES)).decode("ascii")


@dataclass(frozen=True)
class CredentialRef:
    """Reference to a stored credential. Safe for graph JSON, exports,
    events, and audit rows -- carries no secret material."""

    credential_id: str
    generation: int
    purpose: str


class CredentialStore:
    """Write-only credential storage over the designer DB store."""

    def __init__(self, db: Any, key_material: bytes, key_id: str) -> None:
        self._db = db
        self._aesgcm = AESGCM(key_material)
        self._key_id = key_id

    async def store(
        self,
        *,
        owner_user_id: str,
        purpose: str,
        kind: str,
        plaintext: str,
        label: str = "",
    ) -> CredentialRef:
        """Encrypt ``plaintext`` and persist it; returns a safe reference.

        AAD binds version|owner|purpose|generation so a ciphertext cannot
        be decrypted under a different binding (or after a generation bump).
        The row is inserted complete (nonce+ciphertext in the INSERT) so no
        intermediate unencrypted-material state ever exists.
        """
        credential_id = str(uuid.uuid4())
        generation = 1
        nonce = os.urandom(_NONCE_BYTES)
        aad = _aad(owner_user_id, purpose, credential_id, generation)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
        row = await self._db.insert_credential(
            owner_user_id=owner_user_id,
            purpose=purpose,
            kind=kind,
            key_id=self._key_id,
            label=label,
            credential_id=credential_id,
            nonce=nonce,
            ciphertext=ciphertext,
        )
        return CredentialRef(
            credential_id=credential_id, generation=int(row["generation"]), purpose=purpose
        )

    async def resolve_plaintext(
        self, *, actor_user_id: str, ref: CredentialRef
    ) -> str:
        """Decrypt for server-side use only (internal; no API route returns this).

        Authorizes access before decrypting: only the owner (or an
        explicitly authorized actor resolved upstream) may resolve a
        credential, and the stored generation must match the reference.
        """
        row = await self._db.load_credential(ref.credential_id)
        if row is None or str(row["status"]) != "active":
            raise LookupError("credential not available")
        if str(row["owner_user_id"]) != actor_user_id:
            raise PermissionError("credential is not owned by the requesting actor")
        if int(row["generation"]) != ref.generation:
            raise LookupError("credential generation changed; rebuild required")
        aad = _aad(
            str(row["owner_user_id"]),
            str(row["purpose"]),
            str(row["credential_id"]),
            int(row["generation"]),
        )
        try:
            plaintext = self._aesgcm.decrypt(
                bytes(row["nonce"]), bytes(row["ciphertext"]), aad
            )
        except Exception as exc:
            # Reject authentication-tag failures without fallback (R09).
            raise LookupError("credential integrity check failed") from exc
        return plaintext.decode("utf-8")

    async def rotate(
        self, *, actor_user_id: str, ref: CredentialRef, new_plaintext: str
    ) -> CredentialRef:
        """Store a new secret under the same credential_id with generation+1.

        Never exposes old or new secret; callers mark dependent runtimes
        stale and rebuild on the next acquisition (Safety note 2).
        """
        row = await self._db.load_credential(ref.credential_id)
        if row is None or str(row["status"]) != "active":
            raise LookupError("credential not available")
        if str(row["owner_user_id"]) != actor_user_id:
            raise PermissionError("credential is not owned by the requesting actor")
        new_generation = int(row["generation"]) + 1
        nonce = os.urandom(_NONCE_BYTES)
        aad = _aad(
            str(row["owner_user_id"]), str(row["purpose"]),
            str(row["credential_id"]), new_generation,
        )
        ciphertext = self._aesgcm.encrypt(nonce, new_plaintext.encode("utf-8"), aad)
        await self._db.rotate_credential(
            credential_id=ref.credential_id,
            nonce=nonce,
            ciphertext=ciphertext,
            new_generation=new_generation,
        )
        return CredentialRef(
            credential_id=ref.credential_id, generation=new_generation,
            purpose=str(row["purpose"]),
        )

    async def revoke(self, *, actor_user_id: str, ref: CredentialRef) -> None:
        """Mark the credential revoked immediately (Safety note 2)."""
        row = await self._db.load_credential(ref.credential_id)
        if row is None:
            raise LookupError("credential not found")
        if str(row["owner_user_id"]) != actor_user_id:
            raise PermissionError("credential is not owned by the requesting actor")
        await self._db.revoke_credential(ref.credential_id)


def _aad(owner_user_id: str, purpose: str, credential_id: str, generation: int) -> bytes:
    return b"|".join(
        [_AAD_VERSION, owner_user_id.encode("utf-8"), purpose.encode("utf-8"),
         credential_id.encode("utf-8"), str(generation).encode("utf-8")]
    )


def new_session_token() -> str:
    """256 bits of entropy for opaque Designer session/CSRF tokens."""
    return secrets.token_urlsafe(32)
