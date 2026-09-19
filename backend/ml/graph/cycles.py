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


def cycle_members(edges: Iterable[tuple[Node, Node]]) -> set[Node]:
    return {node for component in cycles(edges) for node in component}
