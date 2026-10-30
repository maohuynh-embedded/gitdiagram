from archdiag.arch.components import Component
from archdiag.arch.config import load_architecture
from archdiag.arch.rules import check_rules, strongly_connected
from archdiag.model.schema_v2 import EdgeV2


def comp(cid: str, layer: str, kind: str = "module") -> Component:
    return Component(cid, cid, (cid,), (f"{cid}/a.c",), layer, kind, "declared")


COMPONENTS = [
    comp("ui", "presentation"),
    comp("usecases", "application"),
    comp("model", "domain"),
    comp("can", "infrastructure"),
    comp("tests", "tests", "test"),
]


def dep(source: str, target: str, kind: str = "depends") -> EdgeV2:
    return EdgeV2.model_validate(
        {
            "id": f"{source}__{kind}__{target}",
            "from": source,
            "to": target,
            "kind": kind,
            "weight": 1,
            "evidence_total": 1,
        }
    )


CONFIG = load_architecture(
    {
        "layers": ["presentation", "application", "domain", "infrastructure"],
        "components": [{"name": c.id, "paths": [f"{c.id}/**"]} for c in COMPONENTS],
        "rules": [{"from": "domain", "deny": ["infrastructure"]}],
    }
)


def test_clean_downward_dependencies_have_no_violations() -> None:
    edges = [
        dep("ui", "usecases"),
        dep("usecases", "model"),
        dep("usecases", "can"),
        dep("tests", "ui", "test"),
    ]
    assert check_rules(COMPONENTS, edges, CONFIG) == []


def test_layer_order_deny_and_cycle() -> None:
    edges = [dep("model", "usecases"), dep("usecases", "model"), dep("model", "can")]
    violations = check_rules(COMPONENTS, edges, CONFIG)
    found = sorted((v.rule, v.source, v.target) for v in violations)
    assert found == [
        ("cycle", "model", "usecases"),
        ("cycle", "usecases", "model"),
        ("deny", "model", "can"),
        ("layer-order", "model", "usecases"),
    ]
    layer = next(v for v in violations if v.rule == "layer-order")
    assert layer.edge_id == "model__depends__usecases"
    assert "higher layer 'application'" in layer.message


def test_allow_upward_disables_layer_order() -> None:
    config = load_architecture({"layers": ["application", "domain"], "allow_upward": True})
    assert check_rules(COMPONENTS, [dep("model", "usecases")], config) == []


def test_unassigned_files_are_reported() -> None:
    stray = Component(
        "unassigned", "unassigned", (), ("tools/a.py", "tools/b.py"), None, "unassigned", "inferred"
    )
    violations = check_rules([*COMPONENTS, stray], [], CONFIG)
    assert [(v.rule, v.evidence_total) for v in violations] == [("unassigned", 2)]


def test_tarjan_finds_nested_cycles() -> None:
    sccs = strongly_connected(
        ["a", "b", "c", "d", "e"],
        [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e"), ("e", "d")],
    )
    assert sorted(sccs) == [["a", "b", "c"], ["d", "e"]]


def test_tarjan_handles_long_chains() -> None:
    nodes = [f"n{i}" for i in range(5000)]
    edges = [(nodes[i], nodes[i + 1]) for i in range(4999)] + [("n4999", "n0")]
    assert len(strongly_connected(nodes, edges)) == 1
