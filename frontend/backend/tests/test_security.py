"""Crypto primitives, JWT claim handling, and the enumeration defenses.

These are the tests that would catch the security mistakes that actually get
made: an unverified audience, a missing `exp`, a password policy that lets
`passwordpassword` through, or a timing pad that pads the wrong thing.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from api.errors import TokenExpired, Unauthenticated, ValidationFailed
from core.config import Settings
from core.security import (
    burn_password_cpu,
    constant_time_equals,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    mint_access_token,
    new_refresh_token,
    pad_to_target,
    peek_token_subject,
    sha256_hex,
    sign_media_id,
    validate_password,
    verify_media_signature,
    verify_password,
)

USER_ID = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
ORG_ID = uuid.UUID("00000000-0000-4000-8000-0000000000bb")


# ── password hashing ─────────────────────────────────────────────────────────


def test_hash_verify_round_trip() -> None:
    digest = hash_password("a-perfectly-fine-password")
    ok, needs_rehash = verify_password("a-perfectly-fine-password", digest)
    assert ok is True
    assert needs_rehash is False


def test_wrong_password_rejected() -> None:
    digest = hash_password("a-perfectly-fine-password")
    ok, _ = verify_password("a-perfectly-fine-passworD", digest)
    assert ok is False


def test_hash_is_argon2id_and_salted() -> None:
    first = hash_password("same-input-twice-here")
    second = hash_password("same-input-twice-here")
    assert first.startswith("$argon2id$"), "must be argon2id, not argon2i or argon2d"
    assert first != second, "per-hash salt means identical inputs differ"


def test_malformed_hash_does_not_raise() -> None:
    """A corrupted row must fail the login, not 500 the endpoint."""
    ok, needs_rehash = verify_password("anything", "not-a-hash")
    assert (ok, needs_rehash) == (False, False)


def test_burn_password_cpu_costs_real_time() -> None:
    """The nonexistent-user path must spend comparable CPU to a real verify.

    Without this, "no such email" returns in microseconds and the response
    padding cannot hide it — the pad would be the only thing taking time, and
    the variance would leak.
    """
    digest = hash_password("timing-comparison-password")

    start = time.monotonic()
    verify_password("timing-comparison-password", digest)
    real = time.monotonic() - start

    start = time.monotonic()
    burn_password_cpu()
    dummy = time.monotonic() - start

    # Same order of magnitude is the requirement, not equality.
    assert dummy > real * 0.4


# ── password policy ──────────────────────────────────────────────────────────


def test_short_password_rejected() -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_password("short1234")
    assert "password" in exc.value.details["fields"]


def test_long_but_common_password_rejected() -> None:
    """The denylist's real job: entries long enough to pass the length rule."""
    with pytest.raises(ValidationFailed):
        validate_password("passwordpassword")
    with pytest.raises(ValidationFailed):
        validate_password("qwertyuiop1234")


def test_absurdly_long_password_rejected() -> None:
    """argon2 will happily hash 10 MB and spend real CPU doing it."""
    with pytest.raises(ValidationFailed):
        validate_password("a" * 5_000)


def test_reasonable_password_accepted() -> None:
    validate_password("countersign-ships-at-dawn")


def test_no_composition_rules() -> None:
    """A long all-lowercase passphrase is fine; NIST dropped composition rules."""
    validate_password("seventeen purple bicycles")


# ── access tokens ────────────────────────────────────────────────────────────


def test_mint_and_decode(settings: Settings) -> None:
    token, expires_in = mint_access_token(
        user_id=USER_ID,
        org_id=ORG_ID,
        role="analyst",
        permissions=["insights:read", "ablation:run"],
        settings=settings,
    )
    assert expires_in == settings.access_token_ttl_seconds

    principal = decode_access_token(token, settings)
    assert principal.user_id == USER_ID
    assert principal.org_id == ORG_ID
    assert principal.role == "analyst"
    assert principal.can("ablation:read") is False
    assert principal.can("ablation:run") is True
    assert principal.jti


def test_claims_match_the_brief(settings: Settings) -> None:
    token, _ = mint_access_token(
        user_id=USER_ID, org_id=ORG_ID, role="owner", permissions=[], settings=settings
    )
    claims = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )
    assert set(claims) == {"sub", "org", "role", "perms", "iat", "exp", "jti", "iss", "aud"}
    assert claims["iss"] == settings.jwt_issuer
    assert claims["aud"] == settings.jwt_audience


def test_expired_token_raises_token_expired(settings: Settings) -> None:
    """Distinct from UNAUTHENTICATED so the frontend refreshes silently."""
    past = datetime.now(UTC) - timedelta(minutes=30)
    claims = {
        "sub": str(USER_ID),
        "org": str(ORG_ID),
        "role": "viewer",
        "perms": [],
        "iat": int(past.timestamp()),
        "exp": int((past + timedelta(minutes=1)).timestamp()),
        "jti": str(uuid.uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(TokenExpired):
        decode_access_token(token, settings)


def test_wrong_audience_rejected(settings: Settings) -> None:
    claims = _base_claims(settings) | {"aud": "someone-elses-app"}
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(Unauthenticated):
        decode_access_token(token, settings)


def test_wrong_issuer_rejected(settings: Settings) -> None:
    claims = _base_claims(settings) | {"iss": "not-countersign"}
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(Unauthenticated):
        decode_access_token(token, settings)


def test_missing_exp_rejected(settings: Settings) -> None:
    """A token with no exp would otherwise validate forever."""
    claims = _base_claims(settings)
    del claims["exp"]
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(Unauthenticated):
        decode_access_token(token, settings)


def test_wrong_signature_rejected(settings: Settings) -> None:
    token = jwt.encode(_base_claims(settings), "a-different-secret-entirely", algorithm="HS256")
    with pytest.raises(Unauthenticated):
        decode_access_token(token, settings)


def test_alg_none_rejected(settings: Settings) -> None:
    """The classic JWT attack: unsigned token with alg=none."""
    token = jwt.encode(_base_claims(settings), key="", algorithm="none")
    with pytest.raises(Unauthenticated):
        decode_access_token(token, settings)


def test_garbage_token_rejected(settings: Settings) -> None:
    with pytest.raises(Unauthenticated):
        decode_access_token("not.a.jwt", settings)


# ── rate-limit keying ────────────────────────────────────────────────────────


def test_peek_subject_requires_a_valid_signature(settings: Settings) -> None:
    """A forged token must not mint its own rate-limit bucket."""
    forged = jwt.encode(_base_claims(settings), "wrong-secret", algorithm="HS256")
    assert peek_token_subject(forged, settings) is None


def test_peek_subject_works_on_an_expired_token(settings: Settings) -> None:
    """An expired token should still be rate limited, not fall back to per-IP."""
    claims = _base_claims(settings) | {
        "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp())
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert peek_token_subject(token, settings) == str(USER_ID)


# ── refresh tokens ───────────────────────────────────────────────────────────


def test_refresh_token_is_opaque_and_hashed() -> None:
    plaintext, digest = new_refresh_token()
    assert len(plaintext) >= 43, "expect >=32 bytes of entropy, url-safe encoded"
    assert plaintext.count(".") == 0, "must not be a JWT — it has to be revocable"
    assert digest == hash_refresh_token(plaintext)
    assert len(digest) == 64


def test_refresh_tokens_are_unique() -> None:
    tokens = {new_refresh_token()[0] for _ in range(50)}
    assert len(tokens) == 50


def test_constant_time_equals() -> None:
    assert constant_time_equals("abc", "abc") is True
    assert constant_time_equals("abc", "abd") is False
    assert constant_time_equals("abc", "abcd") is False


# ── response timing ──────────────────────────────────────────────────────────


def test_pad_to_target_reaches_the_deadline() -> None:
    started = time.monotonic()
    pad_to_target(started, 120)
    assert (time.monotonic() - started) >= 0.115


def test_pad_to_target_does_not_extend_slow_work() -> None:
    """Overshoot is not corrected — a slow request is a load signal, not an oracle."""
    started = time.monotonic() - 1.0
    before = time.monotonic()
    pad_to_target(started, 100)
    assert (time.monotonic() - before) < 0.02


# ── signed media urls ────────────────────────────────────────────────────────


def test_media_signature_round_trip(settings: Settings) -> None:
    file_id = str(uuid.uuid4())
    expires_at, signature = sign_media_id(file_id, settings)
    assert verify_media_signature(file_id, expires_at, signature, settings) is True


def test_media_signature_rejects_a_different_file(settings: Settings) -> None:
    expires_at, signature = sign_media_id("file-a", settings)
    assert verify_media_signature("file-b", expires_at, signature, settings) is False


def test_media_signature_rejects_an_extended_expiry(settings: Settings) -> None:
    """Bumping `exp` in the query string must invalidate the signature."""
    file_id = str(uuid.uuid4())
    expires_at, signature = sign_media_id(file_id, settings)
    assert verify_media_signature(file_id, expires_at + 86_400, signature, settings) is False


def test_media_signature_rejects_expired(settings: Settings) -> None:
    file_id = str(uuid.uuid4())
    _, signature = sign_media_id(file_id, settings)
    assert verify_media_signature(file_id, int(time.time()) - 5, signature, settings) is False


# ── content hashing ──────────────────────────────────────────────────────────


def test_sha256_hex_stable_across_str_and_bytes() -> None:
    assert sha256_hex("abc") == sha256_hex(b"abc")
    assert len(sha256_hex("abc")) == 64


# ── helpers ──────────────────────────────────────────────────────────────────


def _base_claims(settings: Settings) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "sub": str(USER_ID),
        "org": str(ORG_ID),
        "role": "analyst",
        "perms": ["insights:read"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
        "jti": str(uuid.uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
