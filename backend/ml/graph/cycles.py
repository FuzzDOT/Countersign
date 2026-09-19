"""Cycle detection over the ownership graph.

Frontend brief §9 explicitly forbids client-side cycle detection, so Tarjan
runs here and the API ships the answer. Used twice: by `ml/cascade/routing.py`
to decide that a relation sits inside an ownership loop, and by
`GET /api/v1/graph` to render the loop.

Iterative rather than recursive. A 400-document corpus can produce an
ownership chain longer than Python's default recursion limit, and the failure
mode — a RecursionError inside an ingest job — is a job card that says
"failed" for a reason nobody can read.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from typing import TypeVar

Node = TypeVar("Node", bound=Hashable)


def strongly_connected_components(
    edges: Iterable[tuple[Node, Node]],
) -> list[list[Node]]:
    """Tarjan's SCCs over a directed edge list, largest first.

    Every component is returned, including singletons — callers decide what
    counts as a cycle, because a self-loop is one and a lone node is not.
    """
    successors: dict[Node, list[Node]] = {}
    for source, target in edges:
        successors.setdefault(source, []).append(target)
        successors.setdefault(target, [])

    index_of: dict[Node, int] = {}
    low_link: dict[Node, int] = {}
    on_stack: set[Node] = set()
    stack: list[Node] = []
    components: list[list[Node]] = []
    counter = 0

    for root in successors:
        if root in index_of:
            continue

        # (node, iterator over its successors). The explicit frame stack is
        # what keeps a long ownership chain from overflowing Python's.
        work: list[tuple[Node, int]] = [(root, 0)]
        index_of[root] = low_link[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)

        while work:
            node, position = work[-1]
            neighbours = successors[node]

            if position < len(neighbours):
                work[-1] = (node, position + 1)
                neighbour = neighbours[position]
                if neighbour not in index_of:
                    index_of[neighbour] = low_link[neighbour] = counter
                    counter += 1
                    stack.append(neighbour)
                    on_stack.add(neighbour)
                    work.append((neighbour, 0))
                elif neighbour in on_stack:
                    low_link[node] = min(low_link[node], index_of[neighbour])
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                low_link[parent] = min(low_link[parent], low_link[node])

            if low_link[node] == index_of[node]:
                component: list[Node] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)

    components.sort(key=len, reverse=True)
    return components


def cycles(edges: Iterable[tuple[Node, Node]]) -> list[list[Node]]:
    """Components that actually contain a cycle.

    A component of two or more nodes always does. A singleton does only if
    the node points at itself — which in an ownership graph means a company
    recorded as its own parent, worth surfacing rather than dropping.
    """
    edge_list = list(edges)
    self_loops = {source for source, target in edge_list if source == target}
    return [
        component
        for component in strongly_connected_components(edge_list)
        if len(component) > 1 or component[0] in self_loops
    ]


def cycle_members(edges: Iterable[tuple[Node, Node]], *, min_length: int = 2) -> set[Node]:
    """Nodes sitting on a cycle of at least `min_length` hops.

    The minimum matters: a two-node loop is a different claim from a
    three-node one. Two companies that own each other is an anomaly however
    you look at it; two companies that pay each other is ordinary trade.
    """
    edge_list = list(edges)
    if min_length <= 2:
        return {node for component in cycles(edge_list) for node in component}
    return {
        node for cycle in simple_cycles(edge_list) if len(cycle) >= min_length for node in cycle
    }


def simple_cycles(
    edges: Iterable[tuple[Node, Node]], *, max_length: int = 5, limit: int = 20
) -> list[list[Node]]:
    """Distinct simple cycles up to `max_length`, shortest first.

    Tarjan answers "are these nodes mutually reachable", which is the right
    question for routing and the wrong one for drawing. In the demo corpus
    the funds subgraph has a single five-node strongly connected component,
    and rendering that as one blob loses the three-hop ring inside it —
    which is the thing worth pointing at.

    So: find the components first (cheap, and it bounds the search), then
    enumerate simple cycles *within* each one. Bounded by length and count
    because cycle enumeration is exponential in general and this runs on a
    request.
    """
    edge_list = list(edges)
    successors: dict[Node, list[Node]] = {}
    for source, target in edge_list:
        successors.setdefault(source, []).append(target)
        successors.setdefault(target, [])

    found: list[list[Node]] = []
    # Canonical rotation of each cycle, so A->B->C and B->C->A are one entry.
    seen: set[tuple[Node, ...]] = set()

    for component in strongly_connected_components(edge_list):
        members = set(component)
        if len(members) == 1 and component[0] not in successors.get(component[0], ()):
            continue

        for start in sorted(members, key=str):
            stack: list[tuple[Node, list[Node]]] = [(start, [start])]
            while stack:
                node, path = stack.pop()
                for neighbour in successors.get(node, ()):
                    if neighbour not in members:
                        continue
                    # `len(path) >= 2` is the ordinary case; `node == start`
                    # with a one-element path is a self-loop, which is a
                    # cycle of length one and worth surfacing.
                    if neighbour == start and (len(path) >= 2 or node == start):
                        canonical = _canonical_rotation(path)
                        if canonical not in seen:
                            seen.add(canonical)
                            found.append(list(path))
                            if len(found) >= limit:
                                return sorted(found, key=len)
                        continue
                    if neighbour in path or len(path) >= max_length:
                        continue
                    stack.append((neighbour, [*path, neighbour]))

    return sorted(found, key=len)


def _canonical_rotation(path: list[Node]) -> tuple[Node, ...]:
    """The rotation starting at the smallest node, so rotations dedupe.

    Direction is *not* normalized: A->B->C and C->B->A are different claims
    in a directed graph, and collapsing them would hide a reversed edge.
    """
    keys = [str(node) for node in path]
    pivot = keys.index(min(keys))
    return tuple(path[pivot:] + path[:pivot])
