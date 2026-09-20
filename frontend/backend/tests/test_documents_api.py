"""The document and ingest endpoints. Brief §5.

`TestClient` runs FastAPI's background tasks before returning from the
request, so a `POST /documents` here comes back with the pipeline already
finished. That is convenient and slightly unrealistic — in production the 202
returns first — so the job-state assertions below check the *terminal* state
rather than a transitional one.
"""

from __future__ import annotations

import uuid

import pytest

from db.models import UserRole

pytestmark = [pytest.mark.integration, pytest.mark.ml]

TXT = "text/plain"
INVOICE = (
    "Invoice INV-9001\nBrightwater Industrial LLC\nIssued: 2026-09-14\n\n"
    "Brightwater Industrial LLC wired $12,400.00 to Calderon Freight Co on 2026-09-02.\n"
)


def _upload(client, headers, body=INVOICE, filename="invoice.txt", source="invoice"):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/documents",
        headers=headers,
        files=[("files", (filename, body.encode(), TXT))],
        data={"source": source},
    )


# ── upload ───────────────────────────────────────────────────────────────────


def test_upload_returns_202_with_a_job_and_the_documents(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    response = _upload(client, tenant_header)
    assert response.status_code == 202, response.text

    body = response.json()
    assert uuid.UUID(body["job_id"])
    assert len(body["documents"]) == 1
    assert body["documents"][0]["chars"] == len(INVOICE)
    assert body["duplicates_skipped"] == 0


def test_the_upload_runs_the_pipeline_and_the_job_finishes(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    job_id = _upload(client, tenant_header).json()["job_id"]

    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=tenant_header).json()
    assert job["state"] == "done"
    assert job["docs_total"] == job["docs_done"] == 1
    assert job["stage_progress"]["tagging"] == 1.0
    assert job["finished_at"] is not None


def test_a_re_uploaded_document_is_skipped_not_rejected(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    """Re-dropping a folder is a thing people do."""
    _upload(client, tenant_header)
    second = _upload(client, tenant_header).json()
    assert second["documents"] == []
    assert second["duplicates_skipped"] == 1


def test_an_oversized_file_is_413(client, tenant_header, settings) -> None:  # type: ignore[no-untyped-def]
    oversized = "x" * (settings.max_upload_bytes + 1)
    response = _upload(client, tenant_header, body=oversized, filename="big.txt")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_an_unsupported_media_type_is_415(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        "/api/v1/documents",
        headers=tenant_header,
        files=[("files", ("payload.exe", b"MZ\x90\x00", "application/x-msdownload"))],
        data={"source": "invoice"},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA"


def test_an_empty_document_is_422_naming_the_file(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    response = _upload(client, tenant_header, body="   \n ", filename="blank.txt")
    assert response.status_code == 422
    assert "blank.txt" in str(response.json()["error"]["details"])


def test_a_bad_received_at_is_422(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        "/api/v1/documents",
        headers=tenant_header,
        files=[("files", ("a.txt", INVOICE.encode(), TXT))],
        data={"source": "invoice", "received_at": "the fourteenth"},
    )
    assert response.status_code == 422


def test_a_viewer_cannot_upload(client, tenant, auth_header) -> None:  # type: ignore[no-untyped-def]
    headers = auth_header(user_id=tenant["user_id"], org=tenant["org_id"], role=UserRole.viewer)
    assert _upload(client, headers).status_code == 403


# ── seed ─────────────────────────────────────────────────────────────────────


def test_seeding_the_demo_scenario_ingests_it(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        "/api/v1/documents/seed",
        headers=tenant_header,
        json={"scenario": "clean_baseline"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert len(body["documents"]) == 28

    job = client.get(f"/api/v1/ingest/jobs/{body['job_id']}", headers=tenant_header).json()
    assert job["state"] == "done"
    assert job["docs_total"] == 28


def test_the_training_corpus_is_not_in_the_scenario_enum(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    """Enforced at the schema, so it never reaches a handler (plan §0)."""
    response = client.post(
        "/api/v1/documents/seed", headers=tenant_header, json={"scenario": "train_corpus"}
    )
    assert response.status_code == 422


def test_pressing_the_demo_button_twice_still_runs_a_job(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    """A judge will double-click it. The second press must do something
    visible rather than return an empty 202 and no job."""
    client.post(
        "/api/v1/documents/seed", headers=tenant_header, json={"scenario": "clean_baseline"}
    )
    second = client.post(
        "/api/v1/documents/seed", headers=tenant_header, json={"scenario": "clean_baseline"}
    ).json()

    assert second["duplicates_skipped"] == 28
    job = client.get(f"/api/v1/ingest/jobs/{second['job_id']}", headers=tenant_header).json()
    assert job["docs_total"] == 28
    assert job["state"] == "done"


# ── list and detail ──────────────────────────────────────────────────────────


def test_documents_list_is_newest_first_and_paginates(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    client.post(
        "/api/v1/documents/seed", headers=tenant_header, json={"scenario": "clean_baseline"}
    )

    first = client.get("/api/v1/documents?limit=10", headers=tenant_header).json()
    assert len(first["data"]) == 10
    assert first["pagination"]["has_more"] is True

    received = [d["received_at"] for d in first["data"]]
    assert received == sorted(received, reverse=True)

    second = client.get(
        f"/api/v1/documents?limit=10&cursor={first['pagination']['next_cursor']}",
        headers=tenant_header,
    ).json()
    assert {d["id"] for d in second["data"]}.isdisjoint({d["id"] for d in first["data"]})


def test_documents_list_filters_by_source_and_title(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    client.post(
        "/api/v1/documents/seed", headers=tenant_header, json={"scenario": "clean_baseline"}
    )
    filtered = client.get("/api/v1/documents?source=email", headers=tenant_header).json()
    assert filtered["data"]
    assert {d["source"] for d in filtered["data"]} == {"email"}

    searched = client.get("/api/v1/documents?q=Saltmarsh", headers=tenant_header).json()
    for document in searched["data"]:
        assert "saltmarsh" in document["title"].casefold()


def test_document_detail_returns_raw_text_and_mentions_that_index_it(  # type: ignore[no-untyped-def]
    client, tenant_header
) -> None:
    """The citation reader's contract: every offset indexes this exact string."""
    upload = _upload(client, tenant_header).json()
    document_id = upload["documents"][0]["id"]

    detail = client.get(f"/api/v1/documents/{document_id}", headers=tenant_header).json()
    assert detail["raw_text"] == INVOICE
    assert detail["mentions"]
    for mention in detail["mentions"]:
        assert detail["raw_text"][mention["char_start"] : mention["char_end"]] == mention["surface"]
        assert 0.0 <= mention["tagger_conf"] <= 1.0


def test_mentions_come_back_in_document_order(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    document_id = _upload(client, tenant_header).json()["documents"][0]["id"]
    detail = client.get(f"/api/v1/documents/{document_id}", headers=tenant_header).json()
    starts = [m["char_start"] for m in detail["mentions"]]
    assert starts == sorted(starts)


def test_another_orgs_document_is_404_not_403(client, tenant_header, auth_header) -> None:  # type: ignore[no-untyped-def]
    """A 403 would confirm the id exists somewhere (brief §5)."""
    document_id = _upload(client, tenant_header).json()["documents"][0]["id"]

    intruder = auth_header(org=uuid.uuid4(), role=UserRole.owner)
    response = client.get(f"/api/v1/documents/{document_id}", headers=intruder)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


# ── jobs ─────────────────────────────────────────────────────────────────────


def test_jobs_list_and_active_filter(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    _upload(client, tenant_header)
    listed = client.get("/api/v1/ingest/jobs", headers=tenant_header).json()
    assert listed["data"]
    assert listed["data"][0]["state"] == "done"

    active = client.get("/api/v1/ingest/jobs?active_only=true", headers=tenant_header).json()
    assert active["data"] == []


def test_retrying_a_finished_job_re_runs_it(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    job_id = _upload(client, tenant_header).json()["job_id"]
    response = client.post(f"/api/v1/ingest/jobs/{job_id}/retry", headers=tenant_header)
    assert response.status_code == 200

    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=tenant_header).json()
    assert job["state"] == "done"
    assert job["docs_done"] == 1


def test_retrying_an_unknown_job_is_404(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    response = client.post(f"/api/v1/ingest/jobs/{uuid.uuid4()}/retry", headers=tenant_header)
    assert response.status_code == 404


def test_another_orgs_job_is_invisible(client, tenant_header, auth_header) -> None:  # type: ignore[no-untyped-def]
    job_id = _upload(client, tenant_header).json()["job_id"]
    intruder = auth_header(org=uuid.uuid4(), role=UserRole.owner)
    assert client.get(f"/api/v1/ingest/jobs/{job_id}", headers=intruder).status_code == 404
