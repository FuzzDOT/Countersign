"""The insight feed and the graph endpoints. Brief §6, §7.

Every filter in §6 is exercised, because the frontend builds its filter panel
and its TanStack query keys against exactly this list — a parameter that
silently does nothing is a bug they cannot see.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db.models import Insight, UserRole

pytestmark = [pytest.mark.integration, pytest.mark.ml]


@pytest.fixture
def seeded(client, tenant_header):  # type: ignore[no-untyped-def]
    """The demo scenario, ingested through the API."""
    response = client.post(
        "/api/v1/documents/seed",
        headers=tenant_header,
        json={"scenario": "meridian_shell_ring"},
    )
    assert response.status_code == 202, response.text
    return response.json()


def _get(client, headers, path):  # type: ignore[no-untyped-def]
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ── the feed ─────────────────────────────────────────────────────────────────


def test_the_feed_returns_insights_with_citations(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    body = _get(client, tenant_header, "/api/v1/insights?limit=5")
    assert len(body["data"]) == 5
    for item in body["data"]:
        citation = item["citation"]
        assert citation["sentence_text"]
        assert citation["char_end"] > citation["char_start"]
        assert citation["document_title"]
        assert item["subject"]["canonical"] and item["object"]["canonical"]
        assert 0.0 <= item["trust"]["confidence"] <= 1.0
        assert item["trust"]["fragility"] is None


def test_the_feed_is_newest_first_by_default(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    body = _get(client, tenant_header, "/api/v1/insights?limit=20")
    stamps = [item["created_at"] for item in body["data"]]
    assert stamps == sorted(stamps, reverse=True)


def test_routing_filter_is_repeatable(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    body = _get(
        client,
        tenant_header,
        "/api/v1/insights?routing=escalate_now&routing=flag_for_review&limit=100",
    )
    assert body["data"]
    assert {item["routing"] for item in body["data"]} <= {"escalate_now", "flag_for_review"}


def test_confidence_bounds_filter(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    body = _get(
        client, tenant_header, "/api/v1/insights?min_confidence=0.8&max_confidence=0.95&limit=100"
    )
    for item in body["data"]:
        assert 0.8 <= item["trust"]["confidence"] <= 0.95


def test_min_vacuity_filter(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    body = _get(client, tenant_header, "/api/v1/insights?min_vacuity=0.2&limit=100")
    for item in body["data"]:
        assert item["trust"]["vacuity"] >= 0.2


def test_relation_filter(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    body = _get(client, tenant_header, "/api/v1/insights?relation=OWNED_BY&limit=100")
    assert body["data"]
    assert {item["relation"] for item in body["data"]} == {"OWNED_BY"}


def test_resolved_by_filter(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    body = _get(client, tenant_header, "/api/v1/insights?resolved_by=classical&limit=100")
    assert {item["resolved_by"] for item in body["data"]} == {"classical"}


def test_entity_filter_matches_either_side(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    """What the graph's "show insights for this node" interaction needs."""
    seed = _get(client, tenant_header, "/api/v1/insights?limit=1")["data"][0]
    entity_id = seed["subject"]["id"]

    body = _get(client, tenant_header, f"/api/v1/insights?entity_id={entity_id}&limit=100")
    assert body["data"]
    for item in body["data"]:
        assert entity_id in (item["subject"]["id"], item["object"]["id"])


def test_document_filter(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    seed = _get(client, tenant_header, "/api/v1/insights?limit=1")["data"][0]
    document_id = seed["citation"]["document_id"]
    body = _get(client, tenant_header, f"/api/v1/insights?document_id={document_id}&limit=100")
    assert {item["citation"]["document_id"] for item in body["data"]} == {document_id}


def test_substring_search_over_the_sentence(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    body = _get(client, tenant_header, "/api/v1/insights?q=Meridian&limit=100")
    assert body["data"]
    for item in body["data"]:
        assert "meridian" in item["citation"]["sentence_text"].casefold()


def test_a_percent_in_the_search_box_is_a_literal(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    """Unescaped, `%` would match everything and look like a broken filter."""
    body = _get(client, tenant_header, "/api/v1/insights?q=%25&limit=100")
    assert body["data"] == []


@pytest.mark.parametrize("field", ["confidence", "vacuity", "fragility", "created_at"])
def test_every_sort_field_works_in_both_directions(  # type: ignore[no-untyped-def]
    client, tenant_header, seeded, field
) -> None:
    for prefix in ("", "-"):
        body = _get(client, tenant_header, f"/api/v1/insights?sort={prefix}{field}&limit=10")
        assert body["data"]


def test_sorting_by_vacuity_actually_orders(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    body = _get(client, tenant_header, "/api/v1/insights?sort=-vacuity&limit=20")
    values = [item["trust"]["vacuity"] for item in body["data"]]
    assert values == sorted(values, reverse=True)


def test_an_unknown_sort_field_is_422(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/insights?sort=rowid", headers=tenant_header).status_code == 422


def test_keyset_pagination_does_not_repeat_rows(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    first = _get(client, tenant_header, "/api/v1/insights?limit=10")
    assert first["pagination"]["has_more"]
    second = _get(
        client,
        tenant_header,
        f"/api/v1/insights?limit=10&cursor={first['pagination']['next_cursor']}",
    )
    assert {i["id"] for i in first["data"]}.isdisjoint({i["id"] for i in second["data"]})


def test_pagination_works_on_a_numeric_sort(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    first = _get(client, tenant_header, "/api/v1/insights?sort=-confidence&limit=10")
    cursor = first["pagination"]["next_cursor"]
    assert cursor
    second = _get(
        client, tenant_header, f"/api/v1/insights?sort=-confidence&limit=10&cursor={cursor}"
    )
    assert {i["id"] for i in first["data"]}.isdisjoint({i["id"] for i in second["data"]})
    assert min(i["trust"]["confidence"] for i in first["data"]) >= max(
        i["trust"]["confidence"] for i in second["data"]
    )


def test_a_cursor_from_a_different_sort_is_422_not_500(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    """A cursor is only valid for the query that produced it, and reusing one
    across sorts has to fail as a client error."""
    numeric = _get(client, tenant_header, "/api/v1/insights?sort=-confidence&limit=5")
    response = client.get(
        f"/api/v1/insights?limit=5&cursor={numeric['pagination']['next_cursor']}",
        headers=tenant_header,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_a_garbage_cursor_is_422(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    assert (
        client.get("/api/v1/insights?cursor=notacursor", headers=tenant_header).status_code == 422
    )


def test_another_org_sees_nothing(client, seeded, auth_header) -> None:  # type: ignore[no-untyped-def]
    intruder = auth_header(org=uuid.uuid4(), role=UserRole.owner)
    assert _get(client, intruder, "/api/v1/insights")["data"] == []


# ── stats ────────────────────────────────────────────────────────────────────


def test_stats_agree_with_the_feed(client, tenant_header, seeded, db_session, tenant) -> None:  # type: ignore[no-untyped-def]
    stats = _get(client, tenant_header, "/api/v1/insights/stats")
    actual = (
        db_session.execute(select(Insight).where(Insight.org_id == tenant["org_id"]))
        .scalars()
        .all()
    )

    assert stats["total"] == len(actual)
    assert sum(stats["by_routing"].values()) == len(actual)
    assert sum(stats["by_resolver"].values()) == len(actual)
    assert stats["documents_ingested"] == 34
    assert 0.0 <= stats["mean_confidence"] <= 1.0


def test_stats_include_every_bucket_even_at_zero(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    """A missing key makes the frontend render "undefined" where a 0 belongs."""
    stats = _get(client, tenant_header, "/api/v1/insights/stats")
    assert set(stats["by_routing"]) == {"auto_file", "flag_for_review", "escalate_now"}
    assert set(stats["by_resolver"]) == {"classical", "nemotron"}


def test_stats_route_wins_over_the_uuid_path(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    """Declared before `/{insight_id}`, or "stats" is parsed as a UUID."""
    assert client.get("/api/v1/insights/stats", headers=tenant_header).status_code == 200


# ── detail ───────────────────────────────────────────────────────────────────


def test_detail_carries_attention_parallel_to_tokens(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    listing = _get(client, tenant_header, "/api/v1/insights?limit=20")
    target = next(i for i in listing["data"] if i["attention_available"])

    detail = _get(client, tenant_header, f"/api/v1/insights/{target['id']}")
    assert detail["tokens"]
    assert detail["attention"]
    for edge in detail["attention"]:
        assert edge["src_token"] == detail["tokens"][edge["src_idx"]]
        assert edge["dst_token"] == detail["tokens"][edge["dst_idx"]]


def test_detail_includes_the_graph_neighbourhood(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    listing = _get(client, tenant_header, "/api/v1/insights?limit=1")
    detail = _get(client, tenant_header, f"/api/v1/insights/{listing['data'][0]['id']}")
    neighbourhood = detail["graph_neighborhood"]
    assert detail["subject"]["id"] in neighbourhood["node_ids"]
    assert detail["object"]["id"] in neighbourhood["node_ids"]
    assert neighbourhood["depth"] == 2


def test_detail_starts_with_empty_history(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    """Ablation is Stage 7 and fragility is Stage 5; both are empty, not
    absent — the frontend renders the sections either way."""
    listing = _get(client, tenant_header, "/api/v1/insights?limit=1")
    detail = _get(client, tenant_header, f"/api/v1/insights/{listing['data'][0]['id']}")
    assert detail["ablation_history"] == []
    assert detail["fragility_trials"] == []


def test_an_unknown_insight_is_404_with_its_own_code(client, tenant_header) -> None:  # type: ignore[no-untyped-def]
    response = client.get(f"/api/v1/insights/{uuid.uuid4()}", headers=tenant_header)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INSIGHT_NOT_FOUND"


def test_another_orgs_insight_is_404_not_403(client, tenant_header, seeded, auth_header) -> None:  # type: ignore[no-untyped-def]
    listing = _get(client, tenant_header, "/api/v1/insights?limit=1")
    intruder = auth_header(org=uuid.uuid4(), role=UserRole.owner)
    response = client.get(f"/api/v1/insights/{listing['data'][0]['id']}", headers=intruder)
    assert response.status_code == 404


# ── graph ────────────────────────────────────────────────────────────────────


def test_the_graph_returns_parties_edges_and_cycles(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    graph = _get(client, tenant_header, "/api/v1/graph")
    assert graph["nodes"] and graph["edges"]
    assert graph["truncated"] is False

    for node in graph["nodes"]:
        assert node["entity_type"] in ("ORG", "PERSON")
        assert 0.0 <= node["risk"] <= 1.0
        assert node["degree"] >= 0

    ids = {node["id"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source"] in ids and edge["target"] in ids
        assert edge["weight"] >= 1
        assert edge["insight_ids"]


def test_the_demo_corpus_contains_a_ring(client, tenant_header, seeded) -> None:
    """The single most legible piece of evidence in the demo.

    Ownership recovers a two-hop chain — the third edge is band-D syntax the
    model has never seen — so the loop that closes is the funds one. Either
    counts: what matters is that a cycle is found server-side and shipped.
    """
    graph = _get(client, tenant_header, "/api/v1/graph")
    names = {node["id"]: node["canonical"] for node in graph["nodes"]}
    ring = {"Meridian Supply LLC", "Advent Holdings", "Kestrel Registry Ltd"}

    assert graph["cycles"], "no cycle found in the demo corpus"
    assert any(
        ring <= {names[node] for node in cycle["node_ids"]} for cycle in graph["cycles"]
    ), f"no cycle through the ring: {[[names[n] for n in c['node_ids']] for c in graph['cycles']]}"


def test_value_entities_are_not_graph_nodes(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    """A canvas with a node per invoice amount is unreadable and says
    nothing."""
    graph = _get(client, tenant_header, "/api/v1/graph")
    assert all(node["entity_type"] in ("ORG", "PERSON") for node in graph["nodes"])


def test_the_graph_honours_its_filters(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    graph = _get(client, tenant_header, "/api/v1/graph?relation=OWNED_BY")
    assert {edge["relation"] for edge in graph["edges"]} <= {"OWNED_BY"}

    confident = _get(client, tenant_header, "/api/v1/graph?min_confidence=0.9")
    assert all(edge["confidence"] >= 0.9 for edge in confident["edges"])


def test_a_root_narrows_the_graph(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    whole = _get(client, tenant_header, "/api/v1/graph")
    root = whole["nodes"][0]["id"]
    narrowed = _get(client, tenant_header, f"/api/v1/graph?root_entity_id={root}&depth=1")
    assert len(narrowed["nodes"]) <= len(whole["nodes"])
    assert root in {node["id"] for node in narrowed["nodes"]}


def test_limit_nodes_sets_the_truncated_flag(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    graph = _get(client, tenant_header, "/api/v1/graph?limit_nodes=2")
    assert graph["truncated"] is True
    assert len(graph["nodes"]) == 2


def test_entity_detail_shows_what_coreference_merged(client, tenant_header, seeded) -> None:  # type: ignore[no-untyped-def]
    graph = _get(client, tenant_header, "/api/v1/graph")
    node = next(n for n in graph["nodes"] if n["canonical"] == "Meridian Supply LLC")

    detail = _get(client, tenant_header, f"/api/v1/graph/entities/{node['id']}")
    assert detail["entity"]["canonical"] == "Meridian Supply LLC"
    assert detail["entity"]["first_seen"]
    assert detail["neighbors"]
    assert detail["documents"]
    assert detail["insight_count"] > 0
    for neighbour in detail["neighbors"]:
        assert neighbour["direction"] in ("in", "out")
        assert neighbour["insight_ids"]


def test_a_value_entity_is_not_addressable_as_a_node(  # type: ignore[no-untyped-def]
    client, tenant_header, seeded, db_session, tenant
) -> None:
    from db.models import Entity

    money = (
        db_session.execute(
            select(Entity).where(Entity.org_id == tenant["org_id"], Entity.entity_type == "MONEY")
        )
        .scalars()
        .first()
    )
    assert money is not None

    response = client.get(f"/api/v1/graph/entities/{money.id}", headers=tenant_header)
    assert response.status_code == 404


def test_another_orgs_entity_is_404(client, tenant_header, seeded, auth_header) -> None:  # type: ignore[no-untyped-def]
    graph = _get(client, tenant_header, "/api/v1/graph")
    intruder = auth_header(org=uuid.uuid4(), role=UserRole.owner)
    response = client.get(f"/api/v1/graph/entities/{graph['nodes'][0]['id']}", headers=intruder)
    assert response.status_code == 404


def test_a_viewer_can_read_the_graph(client, tenant, auth_header, seeded) -> None:  # type: ignore[no-untyped-def]
    """`graph:read` is in every role (brief §4)."""
    viewer = auth_header(org=tenant["org_id"], role=UserRole.viewer)
    assert client.get("/api/v1/graph", headers=viewer).status_code == 200
