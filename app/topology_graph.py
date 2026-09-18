"""Pure dependency-graph logic (stdlib only, no DB) so it is unit-testable.

An edge (source, target) means: source DEPENDS ON target.
If X fails, everything that transitively depends on X is structurally at risk.
That is structure, not measured impact — measured blast radius comes from
experiments (see the experiment engine).
"""
import json
from collections import defaultdict, deque
from typing import Iterable

Edge = tuple[str, str]


class TopologyError(ValueError):
    pass


def validate(nodes: Iterable[str], edges: Iterable[Edge]) -> None:
    """Raise TopologyError on unknown endpoints, self-edges, duplicates, or (with a
    readable cycle path) dependency cycles."""
    node_set = set(nodes)
    edge_list = list(edges)

    seen = set()
    for source, target in edge_list:
        if source not in node_set:
            raise TopologyError(f"edge {source} -> {target}: unknown source '{source}'")
        if target not in node_set:
            raise TopologyError(f"edge {source} -> {target}: unknown target '{target}'")
        if source == target:
            raise TopologyError(f"self-dependency on '{source}'")
        if (source, target) in seen:
            raise TopologyError(f"duplicate edge {source} -> {target}")
        seen.add((source, target))

    cycle = find_cycle(node_set, edge_list)
    if cycle:
        raise TopologyError("dependency cycle: " + " -> ".join(cycle))


def find_cycle(nodes: Iterable[str], edges: Iterable[Edge]) -> list[str] | None:
    """Return one cycle as [a, b, ..., a], or None."""
    out = defaultdict(list)
    for source, target in edges:
        out[source].append(target)

    WHITE, GREY, BLACK = 0, 1, 2
    colour = {n: WHITE for n in nodes}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        colour[node] = GREY
        stack.append(node)
        for nxt in sorted(out[node]):
            if colour[nxt] == GREY:
                return stack[stack.index(nxt):] + [nxt]
            if colour[nxt] == WHITE:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        colour[node] = BLACK
        return None

    for node in sorted(colour):
        if colour[node] == WHITE:
            found = visit(node)
            if found:
                return found
    return None


def _reach(start: str, adjacency: dict[str, list[str]]) -> dict[str, int]:
    """BFS from start; returns {node: hop distance} excluding start itself."""
    distance: dict[str, int] = {}
    queue = deque([(start, 0)])
    visited = {start}
    while queue:
        node, hops = queue.popleft()
        for nxt in adjacency.get(node, ()):
            if nxt not in visited:
                visited.add(nxt)
                distance[nxt] = hops + 1
                queue.append((nxt, hops + 1))
    return distance


def _adjacency(edges: Iterable[Edge], reverse: bool) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        if reverse:
            adjacency[target].append(source)
        else:
            adjacency[source].append(target)
    return adjacency


def dependencies(node: str, edges: Iterable[Edge]) -> dict[str, int]:
    """Everything `node` depends on (transitively): {name: hops}."""
    return _reach(node, _adjacency(edges, reverse=False))


def dependents(node: str, edges: Iterable[Edge]) -> dict[str, int]:
    """Everything that depends on `node` (transitively) — the structural
    blast radius if `node` fails: {name: hops}."""
    return _reach(node, _adjacency(edges, reverse=True))


def impact_ranking(nodes: Iterable[str], edges: Iterable[Edge]) -> list[dict]:
    """Nodes ranked by how many others transitively depend on them."""
    edge_list = list(edges)
    rows = []
    for node in nodes:
        direct = sorted(s for s, t in edge_list if t == node)
        transitive = dependents(node, edge_list)
        rows.append({
            "service": node,
            "direct_dependents": direct,
            "transitive_dependents": sorted(transitive),
            "transitive_count": len(transitive),
            "max_depth": max(transitive.values(), default=0),
        })
    rows.sort(key=lambda r: (-r["transitive_count"], r["service"]))
    return rows


REQUIRED_SERVICE_FIELDS = ("name", "kind")
REQUIRED_DEPENDENCY_FIELDS = ("source", "target", "relation", "evidence")


def load_file(path: str) -> tuple[list[dict], list[dict]]:
    """Read and validate a topology file. Returns (services, dependencies).
    Every dependency must carry evidence; the graph must be a valid DAG."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    services = data.get("services", [])
    dependencies_ = data.get("dependencies", [])

    for svc in services:
        for field in REQUIRED_SERVICE_FIELDS:
            if not svc.get(field):
                raise TopologyError(f"service {svc!r} is missing '{field}'")
    names = [svc["name"] for svc in services]
    if len(names) != len(set(names)):
        raise TopologyError("duplicate service names")

    for dep in dependencies_:
        for field in REQUIRED_DEPENDENCY_FIELDS:
            if not dep.get(field):
                raise TopologyError(f"dependency {dep!r} is missing '{field}' (evidence is mandatory)")

    validate(names, [(d["source"], d["target"]) for d in dependencies_])
    return services, dependencies_
