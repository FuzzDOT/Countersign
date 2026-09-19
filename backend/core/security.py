"""Password hashing, JWT minting/verification, refresh tokens, signed URLs.

Nothing in here talks to the database. `api/v1/auth.py` composes these
primitives with persistence; keeping them separate means the crypto is unit
testable without a live Postgres.

Design notes:

- argon2id at the brief's parameters (§4). `verify_password` returns a
  `needs_rehash` flag so that raising the cost later re-hashes users on their
  next successful login instead of requiring a reset.
- Access tokens are JWTs carrying the permission list, so route guards never
  hit the database to authorize. Refresh tokens are *opaque* random strings —
  a JWT refresh token cannot be revoked without a blocklist, and revocation is
  the entire point of the rotating-family scheme.
- `pad_to_target` gives login a constant wall-clock duration. It computes the
  remaining time against a deadline rather than sleeping a fixed amount after
  the work, because a fixed added sleep still leaks the variable part.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from api.errors import TokenExpired, Unauthenticated, ValidationFailed
from core.config import Settings, get_settings

# ── password hashing ─────────────────────────────────────────────────────────

# Brief §4: time_cost=3, memory_cost=65536 (64 MiB), parallelism=4.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65_536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# A hash of a throwaway value, used to burn equivalent CPU when the email does
# not exist. Without this, "user not found" returns in microseconds while a
# real user costs ~60ms, which is a trivially measurable enumeration oracle
# even with response padding on top.
_DUMMY_HASH = _hasher.hash("countersign-nonexistent-user-placeholder")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> tuple[bool, bool]:
    """Return (is_valid, needs_rehash)."""
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, False
    try:
        return True, _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:  # pragma: no cover
        return True, True


def burn_password_cpu() -> None:
    """Spend the same CPU as a real verification against a nonexistent user."""
    with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        # The verification is *expected* to fail. The point is the CPU it
        # burns, so that a login for a nonexistent user costs what a real one
        # does and cannot be distinguished by timing.
        _hasher.verify(_DUMMY_HASH, "countersign-wrong-password")


# ── password policy ──────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _common_passwords(path_str: str) -> frozenset[str]:
    """Load the common-password denylist, lowercased.

    The shipped file is a curated list of the most-used passwords. Dropping in a
    larger list (e.g. SecLists' top 10k) needs no code change — the loader reads
    whatever is at COMMON_PASSWORDS_PATH, one entry per line, `#` for comments.
    """
    path = Path(path_str)
    if not path.exists():
        return frozenset()
    entries = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            entries.add(stripped.lower())
    return frozenset(entries)


def validate_password(password: str, settings: Settings | None = None) -> None:
    """Raise ValidationFailed if the password does not meet policy.

    Policy is length plus a denylist, deliberately without composition rules
    (one upper, one digit, one symbol). Composition rules push people toward
    `Password1!`, which is in every denylist on earth. NIST dropped them for
    that reason.
    """
    settings = settings or get_settings()
    problems: list[str] = []

    if len(password) < settings.password_min_length:
        problems.append(f"must be at least {settings.password_min_length} characters")
    if len(password) > 256:
        # argon2 will happily hash 10 MB of input and spend real CPU doing it.
        problems.append("must be at most 256 characters")
    if password.strip() != password:
        problems.append("cannot begin or end with whitespace")

    denylist = _common_passwords(str(settings.path(settings.common_passwords_path)))
    if password.lower() in denylist:
        problems.append("is among the most commonly used passwords — pick something else")

    if problems:
        raise ValidationFailed(
            "The password does not meet the policy.",
            details={"fields": {"password": "; ".join(problems)}},
        )


# ── access tokens ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, reconstructed from the access token alone."""

    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    permissions: frozenset[str]
    jti: str

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def mint_access_token(
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    role: str,
    permissions: list[str],
    settings: Settings | None = None,
) -> tuple[str, int]:
    """Return (jwt, expires_in_seconds). Claims match brief §4."""
    settings = settings or get_settings()
    now = datetime.now(UTC)
    expires_in = settings.access_token_ttl_seconds
    claims = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "perms": permissions,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": str(uuid.uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str, settings: Settings | None = None) -> Principal:
    """Verify and decode an access token.

    `aud` and `iss` are both verified, and the required-claims list is
    explicit — a token missing `exp` would otherwise validate forever.
    """
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={
                "require": ["sub", "org", "exp", "iat", "jti", "iss", "aud"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        # Distinct code so the frontend silently refreshes instead of logging
        # the user out (02-FRONTEND-BRIEF.md §4.3).
        raise TokenExpired() from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthenticated("The access token is invalid.") from exc

    try:
        return Principal(
            user_id=uuid.UUID(str(claims["sub"])),
            org_id=uuid.UUID(str(claims["org"])),
            role=str(claims.get("role", "viewer")),
            permissions=frozenset(str(p) for p in claims.get("perms", [])),
            jti=str(claims["jti"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise Unauthenticated("The access token is malformed.") from exc


def peek_token_subject(token: str, settings: Settings | None = None) -> str | None:
    """Best-effort `sub` extraction for rate-limit keying only.

    Signature is still verified — an unverified read would let anyone forge a
    rate-limit bucket and evade the limit — but expiry is not, because an
    expired token should still be rate limited rather than falling back to a
    shared per-IP bucket. Never use the result for authorization.
    """
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"verify_exp": False, "require": ["sub"]},
        )
    except jwt.InvalidTokenError:
        return None
    sub = claims.get("sub")
    return str(sub) if sub else None


# ── refresh tokens ───────────────────────────────────────────────────────────

REFRESH_TOKEN_BYTES = 48


def new_refresh_token() -> tuple[str, str]:
    """Return (plaintext, sha256_hex).

    Only the hash is stored. SHA-256 rather than argon2 is correct here: the
    token is 48 bytes of CSPRNG output, so there is no dictionary to attack and
    nothing for a slow hash to buy — and refresh happens on a hot path where
    64 MiB of argon2 memory per call is not free.
    """
    plaintext = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    return plaintext, hash_refresh_token(plaintext)


def hash_refresh_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ── response timing ──────────────────────────────────────────────────────────


def pad_to_target(started_at: float, target_ms: int) -> None:
    """Sleep until `target_ms` have elapsed since `started_at`.

    Called at the end of every login path — success, wrong password, unknown
    email, locked account — so all four are indistinguishable by timing.
    Overshoot is not corrected; a slow request is not an oracle for which
    branch ran, only for load.
    """
    if target_ms <= 0:
        return
    remaining = (target_ms / 1000.0) - (time.monotonic() - started_at)
    if remaining > 0:
        time.sleep(remaining)


# ── signed media URLs ────────────────────────────────────────────────────────


def sign_media_id(file_id: str, settings: Settings | None = None) -> tuple[int, str]:
    """Return (expires_at_unix, signature) for a generated audio file.

    Used by the voice endpoints (Stage 8) so an `audio_url` can be dropped into
    an `<audio src>` — which cannot carry an Authorization header — without
    making the file world-readable. Bearer auth is accepted as an alternative.
    """
    settings = settings or get_settings()
    expires_at = int(time.time()) + settings.audio_url_ttl_seconds
    return expires_at, _media_signature(file_id, expires_at, settings)


def verify_media_signature(
    file_id: str, expires_at: int, signature: str, settings: Settings | None = None
) -> bool:
    settings = settings or get_settings()
    if expires_at < int(time.time()):
        return False
    expected = _media_signature(file_id, expires_at, settings)
    return hmac.compare_digest(expected, signature)


def _media_signature(file_id: str, expires_at: int, settings: Settings) -> str:
    payload = f"{file_id}|{expires_at}".encode()
    return hmac.new(settings.jwt_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def sha256_hex(data: str | bytes) -> str:
    """Content SHA for document dedupe and Nemotron prompt digests."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()
