"""The job progress websocket. Brief §5.

The live path polls the job row and pushes on change, which reproduces the
contract's "every state transition and every 5 documents" without coupling a
background thread to a socket that may already be gone.
"""

from __future__ import annotations

import uuid

import pytest

from db.models import UserRole

pytestmark = pytest.mark.integration

TXT = "text/plain"
BODY = "Brightwater Industrial LLC wired $12,400.00 to Calderon Freight Co on 2026-09-02.\n"


def test_an_unauthenticated_socket_is_rejected_before_accept(client) -> None:  # type: ignore[no-untyped-def]
    """Closing before accept refuses the handshake, so a caller without a
    valid token never holds an open socket."""
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect) as caught,
        client.websocket_connect(f"/api/v1/ws/jobs/{uuid.uuid4()}?token={'x' * 40}"),
    ):
        pass
    assert caught.value.code == 1008


@pytest.mark.ml
def test_a_finished_job_streams_a_terminal_frame_and_closes(  # type: ignore[no-untyped-def]
    client, tenant, tenant_header, token_factory
) -> None:
    upload = client.post(
        "/api/v1/documents",
        headers=tenant_header,
        files=[("files", ("a.txt", BODY.encode(), TXT))],
        data={"source": "invoice"},
    ).json()

    token = token_factory(user_id=tenant["user_id"], org=tenant["org_id"], role=UserRole.owner)
    frames = []
    with client.websocket_connect(f"/api/v1/ws/jobs/{upload['job_id']}?token={token}") as socket:
        frames.append(socket.receive_json())

    assert frames[-1]["state"] == "done"
    assert frames[-1]["id"] == upload["job_id"]
    assert set(frames[-1]["stage_progress"]) == {
        "tagging",
        "parsing",
        "relating",
        "scoring",
        "routing",
    }


def test_another_orgs_job_closes_the_socket(client, token_factory) -> None:  # type: ignore[no-untyped-def]
    """Indistinguishable from a job that never existed — the same answer
    `get_or_404` gives over HTTP."""
    from starlette.websockets import WebSocketDisconnect

    token = token_factory(org=uuid.uuid4(), role=UserRole.owner)
    with (
        pytest.raises(WebSocketDisconnect) as caught,
        client.websocket_connect(f"/api/v1/ws/jobs/{uuid.uuid4()}?token={token}") as socket,
    ):
        socket.receive_json()
    assert caught.value.code == 1008


def test_mock_mode_streams_the_four_job_fixtures(mock_client, token_factory) -> None:  # type: ignore[no-untyped-def]
    """Frontend dev 2 builds the progress bar against this at hour 4."""
    job_id = uuid.uuid4()
    token = token_factory(role=UserRole.owner)
    states = []
    with mock_client.websocket_connect(f"/api/v1/ws/jobs/{job_id}?token={token}") as socket:
        for _ in range(4):
            frame = socket.receive_json()
            states.append(frame["state"])
            assert frame["id"] == str(job_id)
    assert states == ["queued", "tagging", "relating", "done"]
