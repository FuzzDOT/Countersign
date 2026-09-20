"""Mock mode behaviour.

This is the frontend's entire runtime until Stage 4 lands, so it gets tested
like a product surface rather than a development convenience.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core import ids


def test_pending_route_serves_its_fixture_in_mock_mode(mock_client: TestClient) -> None:
    login = mock_client.post(
        "/api/v1/auth/login",
        json={"email": "ops@meridian.example", "password": "irrelevant-in-mock"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    response = mock_client.get("/api/v1/insights", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["data"], "the insights fixture served an empty page"
    assert "trust" in body["data"][0]
    assert "vacuity" in body["data"][0]["trust"]


def test_mock_login_mints_a_real_token_that_passes_real_auth(
    mock_client: TestClient,
) -> None:
    """The reason mock auth is not itself a fixture.

    The frontend's Authorization header, its refresh loop and its permission
    gating all exercise the genuine code paths in mock mode, so none of that is
    discovered to be broken at hour 12 when the database arrives.
    """
    login = mock_client.post(
        "/api/v1/auth/login", json={"email": "ops@meridian.example", "password": "whatever"}
    )
    assert login.status_code == 200
    payload = login.json()
    assert payload["user"]["id"] == str(ids.DEMO_OWNER_ID)
    assert payload["user"]["org_id"] == str(ids.DEMO_ORG_ID)
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] > 0

    # The token must be accepted by the real dependency, not a mock bypass.
    me = mock_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {payload['access_token']}"}
    )
    assert me.status_code == 200
    assert "insights:read" in me.json()["permissions"]


def test_unauthenticated_requests_are_still_rejected_in_mock_mode(
    mock_client: TestClient,
) -> None:
    """Mock mode must not be an auth bypass — otherwise the frontend builds
    against a permission model that does not exist."""
    assert mock_client.get("/api/v1/insights").status_code == 401
    assert mock_client.get("/api/v1/evals/fragility").status_code == 401


def test_mock_mode_is_a_production_config_error() -> None:
    """`Settings.check_production_safety()` reports MOCK_MODE as a problem.

    Serving fixtures from something labelled production would be the worst
    possible way to discover a misconfigured deploy. The app factory calls
    this and exits via `fatal_config_exit` if the list is non-empty.
    """
    from core.config import Settings

    settings = Settings(
        environment="production",
        mock_mode=True,
        jwt_secret="x" * 48,
        refresh_cookie_secure=True,
    )
    problems = settings.check_production_safety()
    assert any("MOCK_MODE" in problem for problem in problems), problems


def test_development_config_reports_no_production_problems() -> None:
    """The check must be a no-op outside production, or `make dev` would not
    start with the placeholder secret in .env.example."""
    from core.config import Settings

    assert Settings(environment="development", mock_mode=True).check_production_safety() == []


def test_pending_route_is_a_501_in_live_mode(  # type: ignore[no-untyped-def]
    app, client: TestClient, auth_header
) -> None:
    """Live mode must fail honestly rather than serve fixture data.

    A pending endpoint returning plausible-looking mock data outside MOCK_MODE
    is how a demo ends up showing numbers nobody computed.

    The route is chosen from the live route table rather than named here.
    Hardcoding one meant this test silently stopped testing anything the
    moment that stage landed — it did, when `/evals/fragility` was
    implemented in Stage 5 and this started asserting that a working
    endpoint was broken.
    """
    from fastapi.routing import APIRoute

    from api.mock import contract_of

    pending = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and "GET" in (route.methods or ())
        and "{" not in route.path
        and (meta := contract_of(route.endpoint)) is not None
        and meta.pending
    ]
    if not pending:
        pytest.skip("every route is implemented — nothing left to be pending")

    route = pending[0]
    stage = contract_of(route.endpoint).stage  # type: ignore[union-attr]

    response = client.get(route.path, headers=auth_header())
    # 403 if the analyst role lacks the permission, 501 if permitted but unbuilt.
    assert response.status_code in (403, 501), route.path
    if response.status_code == 501:
        body = response.json()["error"]
        assert body["details"]["stage"] == stage
        assert "MOCK_MODE" in body["details"]["hint"]


def test_job_websocket_streams_progress_in_mock_mode(mock_client: TestClient) -> None:
    """The progress bar and the socket-to-polling fallback are fiddly; frontend
    dev 2 should be able to build them at hour 4, not discover the state
    transitions at hour 14."""
    from api.deps import permissions_for
    from core.config import get_settings
    from core.security import mint_access_token
    from db.models import UserRole

    settings = get_settings()
    token, _ = mint_access_token(
        user_id=ids.DEMO_OWNER_ID,
        org_id=ids.DEMO_ORG_ID,
        role=str(UserRole.owner),
        permissions=permissions_for(UserRole.owner),
        settings=settings,
    )
    job_id = ids.stable_uuid("job", "seed", "meridian_shell_ring")

    states: list[str] = []
    with mock_client.websocket_connect(f"/api/v1/ws/jobs/{job_id}?token={token}") as ws:
        for _ in range(4):
            frame = ws.receive_json()
            states.append(frame["state"])
            assert frame["id"] == str(job_id)
            assert "stage_progress" in frame

    assert states == ["queued", "tagging", "relating", "done"]


def test_job_websocket_rejects_a_bad_token(mock_client: TestClient) -> None:
    import pytest
    from starlette.websockets import WebSocketDisconnect

    job_id = ids.stable_uuid("job", "seed", "meridian_shell_ring")
    with (
        pytest.raises(WebSocketDisconnect) as caught,
        mock_client.websocket_connect(f"/api/v1/ws/jobs/{job_id}?token={'x' * 40}") as ws,
    ):
        ws.receive_json()
    assert caught.value.code == 1008
