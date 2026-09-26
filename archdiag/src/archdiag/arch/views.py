"""Views over the static model: system, dependencies (collapsing to layers), interfaces, classes."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from archdiag.arch.aggregate import ACTOR_ID, Aggregation, layer_group_id
from archdiag.arch.components import Component, snake_id
from archdiag.model.schema_v2 import (
    MAX_EVIDENCE,
    MAX_VIEW_NODES,
    EdgeV2,
    Evidence,
    NodeV2,
    View,
)

MAX_CLASSES_PER_VIEW = 25
MAX_EXTERNALS_IN_SYSTEM = 12
_CONFIDENCE_ORDER = {"low": 1, "medium": 2, "high": 3}


@dataclass
class ViewSet:
    nodes: list[NodeV2]
    edges: list[EdgeV2]
    views: list[View] = field(default_factory=list)


def _merge(source: str, target: str, kind: str, parts: Sequence[EdgeV2]) -> EdgeV2:
    evidence: list[Evidence] = sorted(
        (e for p in parts for e in p.evidence), key=lambda e: (e.file, e.line)
    )
    labels = [p.label for p in parts if p.label]
    return EdgeV2.model_validate({
        "id": f"{source}__{kind}__{target}", "from": source, "to": target, "kind": kind,
        "label": ", ".join(dict.fromkeys(labels))[:80] if labels else None,
        "weight": sum(p.weight for p in parts),
        "confidence": max((p.confidence for p in parts), key=lambda c: _CONFIDENCE_ORDER[c]),
        "evidence": evidence[:MAX_EVIDENCE],
        "evidence_total": sum(p.evidence_total for p in parts),
    })


def _lift(edges: Sequence[EdgeV2], mapping: dict[str, str], kinds: set[str]) -> list[EdgeV2]:
    grouped: dict[tuple[str, str, str], list[EdgeV2]] = defaultdict(list)
    for edge in edges:
        if edge.kind not in kinds:
            continue
        source, target = mapping.get(edge.source), mapping.get(edge.target)
        if source and target and source != target:
            grouped[(source, target, edge.kind)].append(edge)
    return [_merge(s, t, k, parts) for (s, t, k), parts in sorted(grouped.items())]


def build_views(components: Sequence[Component], agg: Aggregation, repo_name: str) -> ViewSet:
    nodes = [n for n in agg.nodes if n.level != "class"]
    edges = [e for e in agg.edges if not e.source.startswith("cls_")]
    result = ViewSet(list(nodes), list(edges))
    modules = [c for c in components if c.kind != "test"]
    module_ids = {c.id for c in modules}

    # --- system (L0) ----------------------------------------------------------------------
    component_targets: dict[str, str] = {}
    target_nodes: list[NodeV2] = []
    for target in agg.targets:
        sources = set(target.get("sources", []))  # type: ignore[arg-type]
        owned = [c.id for c in modules if sources.intersection(c.files)]
        if not owned:
            continue
        node_id = snake_id(str(target["name"]), "tgt_")
        if any(n.id == node_id for n in target_nodes):
            continue
        target_nodes.append(NodeV2(
            id=node_id, label=str(target["name"]), kind=str(target["kind"]), level="system",
            paths=[str(target["file"])], metrics={"components": len(owned)},
            description=f"{target['system']} {target['kind']} built from {len(sources)} files",
        ))
        for component_id in owned:
            component_targets.setdefault(component_id, node_id)
    if not target_nodes:
        system_id = snake_id(repo_name, "sys_")
        target_nodes.append(NodeV2(id=system_id, label=repo_name, kind="system", level="system"))
    fallback = target_nodes[0].id
    for component_id in module_ids:
        component_targets.setdefault(component_id, fallback)
    mapping = dict(component_targets)
    externals = [n for n in agg.nodes if n.level == "external"]
    external_weight = defaultdict(int)
    for edge in edges:
        if edge.kind == "uses" and edge.source in module_ids:
            external_weight[edge.target] += edge.weight
    shown = sorted(externals, key=lambda n: (-external_weight[n.id], n.id))[:MAX_EXTERNALS_IN_SYSTEM]
    for node in [*shown, *(n for n in agg.nodes if n.id == ACTOR_ID)]:
        mapping[node.id] = node.id
    system_edges = _lift(edges, mapping, {"depends", "uses", "message"})
    result.nodes.extend(target_nodes)
    result.edges.extend(system_edges)
    system_nodes = [n.id for n in target_nodes] + [n.id for n in shown] + (
        [ACTOR_ID] if any(n.id == ACTOR_ID for n in agg.nodes) else []
    )
    result.views.append(View(
        id="system", title="System context", level="system",
        description="Build targets, the libraries they use and the device they talk to.",
        node_ids=system_nodes, edge_ids=[e.id for e in system_edges],
    ))

    # --- dependencies (L1/L2) -------------------------------------------------------------
    dependency_edges = [
        e for e in edges if e.kind == "depends" and e.source in module_ids and e.target in module_ids
    ]
    if len(modules) <= MAX_VIEW_NODES:
        result.views.append(View(
            id="dependencies", title="Component dependencies", level="component",
            description="Components and the static dependencies between them.",
            node_ids=[c.id for c in modules], edge_ids=[e.id for e in dependency_edges],
        ))
    else:
        layer_of = {c.id: layer_group_id(c.layer or "unlayered") for c in modules}
        layer_nodes: dict[str, NodeV2] = {}
        for component in modules:
            layer_id = snake_id(component.layer or "unlayered", "lyr_")
            node = layer_nodes.get(layer_id)
            if node is None:
                node = NodeV2(id=layer_id, label=component.layer or "unlayered", kind="layer",
                              level="layer", layer=component.layer, metrics={"components": 0})
                layer_nodes[layer_id] = node
            node.metrics["components"] += 1
            layer_of[component.id] = layer_id
        layer_edges = _lift(dependency_edges, layer_of, {"depends"})
        result.nodes.extend(layer_nodes.values())
        result.edges.extend(layer_edges)
        result.views.append(View(
            id="dependencies", title="Layer dependencies", level="layer",
            description=f"{len(modules)} components collapsed into their layers; open a layer "
                        "to see its components.",
            node_ids=list(layer_nodes), edge_ids=[e.id for e in layer_edges],
            collapsed_from="component",
        ))
        for layer_id, node in layer_nodes.items():
            members = [c.id for c in modules if layer_of[c.id] == layer_id][:MAX_VIEW_NODES]
            inside = set(members)
            result.views.append(View(
                id=f"dependencies/{layer_id}", title=f"Layer {node.label}", level="component",
                node_ids=members, parent="dependencies",
                edge_ids=[e.id for e in dependency_edges
                          if e.source in inside and e.target in inside],
            ))

    # --- interfaces -----------------------------------------------------------------------
    message_edges = [e for e in edges if e.kind == "message"]
    if message_edges:
        involved = list(dict.fromkeys(
            n for e in message_edges for n in (e.source, e.target)
        ))[:MAX_VIEW_NODES]
        keep = set(involved)
        result.views.append(View(
            id="interfaces", title="Interfaces and messages", level="component",
            description="Who sends and receives which messages. Pattern-based edges are "
                        "inferences (low confidence).",
            node_ids=involved,
            edge_ids=[e.id for e in message_edges if e.source in keep and e.target in keep],
        ))

    # --- classes per component (L3) -------------------------------------------------------
    class_nodes = {n.id: n for n in agg.nodes if n.level == "class"}
    class_edges = [e for e in agg.edges if e.source.startswith("cls_")]
    used_classes: set[str] = set()
    for component in modules:
        members = sorted(
            (n for n in class_nodes.values() if n.group == component.id),
            key=lambda n: (-n.metrics.get("references", 0), n.label),
        )[:MAX_CLASSES_PER_VIEW]
        ids = {n.id for n in members}
        inner = [e for e in class_edges if e.source in ids and e.target in ids]
        if len(members) < 2 or not inner:
            continue
        used_classes.update(ids)
        result.views.append(View(
            id=f"classes/{component.id}", title=f"Classes in {component.name}", level="class",
            node_ids=[n.id for n in members], edge_ids=[e.id for e in inner],
        ))
    result.nodes.extend(n for n in class_nodes.values() if n.id in used_classes)
    result.edges.extend(
        e for e in class_edges if e.source in used_classes and e.target in used_classes
    )
    return result
