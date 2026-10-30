"""Compare the as-built dependencies with the intended architecture."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from archdiag.arch.components import UNASSIGNED, Component
from archdiag.arch.config import ArchitectureConfig
from archdiag.model.schema_v2 import MAX_EVIDENCE, EdgeV2, Evidence, Violation

STRUCTURAL = "depends"


def _violation(rule: str, edge: EdgeV2, message: str) -> Violation:
    return Violation.model_validate(
        {
            "rule": rule,
            "from": edge.source,
            "to": edge.target,
            "message": message,
            "edge_id": edge.id,
            "evidence": edge.evidence[:MAX_EVIDENCE],
            "evidence_total": edge.evidence_total,
        }
    )


def strongly_connected(nodes: Sequence[str], edges: Sequence[tuple[str, str]]) -> list[list[str]]:
    """Tarjan's algorithm, iterative so deep graphs cannot overflow the stack."""
    graph: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        graph[source].append(target)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0
    for start in nodes:
        if start in index:
            continue
        work = [(start, 0)]
        while work:
            node, child_index = work.pop()
            if child_index == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            recurse = False
            children = graph[node]
            for i in range(child_index, len(children)):
                child = children[i]
                if child not in index:
                    work.append((node, i + 1))
                    work.append((child, 0))
                    recurse = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if recurse:
                continue
            if low[node] == index[node]:
                component = []
                while True:
                    item = stack.pop()
                    on_stack.discard(item)
                    component.append(item)
                    if item == node:
                        break
                result.append(sorted(component))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return result


def check_rules(
    components: Sequence[Component], edges: Sequence[EdgeV2], config: ArchitectureConfig
) -> list[Violation]:
    by_id = {c.id: c for c in components}
    structural = [
        e
        for e in edges
        if e.kind == STRUCTURAL
        and e.source in by_id
        and e.target in by_id
        and by_id[e.source].kind != "test"
        and by_id[e.target].kind != "test"
    ]
    violations: list[Violation] = []

    order = {layer: i for i, layer in enumerate(config.layers)}
    if order and not config.allow_upward:
        for edge in structural:
            source_layer = by_id[edge.source].layer
            target_layer = by_id[edge.target].layer
            if (
                source_layer in order
                and target_layer in order
                and order[source_layer] > order[target_layer]
            ):
                violations.append(
                    _violation(
                        "layer-order",
                        edge,
                        f"'{by_id[edge.source].name}' ({source_layer}) depends on "
                        f"'{by_id[edge.target].name}' in the higher layer '{target_layer}'.",
                    )
                )

    for rule in config.rules:
        denied = set(rule.deny)
        for edge in structural:
            source, target = by_id[edge.source], by_id[edge.target]
            if rule.source not in (source.name, source.layer):
                continue
            hit = next((d for d in (target.name, target.layer) if d in denied), None)
            if hit:
                violations.append(
                    _violation(
                        "deny",
                        edge,
                        f"'{source.name}' must not depend on '{hit}' (rule from '{rule.source}').",
                    )
                )

    ids = [c.id for c in components if c.kind != "test"]
    for scc in strongly_connected(ids, [(e.source, e.target) for e in structural]):
        if len(scc) < 2:
            continue
        members = set(scc)
        names = ", ".join(by_id[i].name for i in scc)
        for edge in structural:
            if edge.source in members and edge.target in members:
                violations.append(_violation("cycle", edge, f"Dependency cycle between: {names}."))

    for component in components:
        if component.kind == UNASSIGNED and component.files:
            sample = [
                Evidence(file=f, line=1, fact="file", source="config", confidence="high")
                for f in component.files[:MAX_EVIDENCE]
            ]
            violations.append(
                Violation.model_validate(
                    {
                        "rule": "unassigned",
                        "from": component.id,
                        "message": f"{len(component.files)} file(s) match no declared component.",
                        "evidence": sample,
                        "evidence_total": len(component.files),
                    }
                )
            )
    return violations
