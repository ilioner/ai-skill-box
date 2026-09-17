#!/usr/bin/env python3
"""Deterministically validate graph structure, references and evidence fields."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

NODE_TYPES = {"capability", "knowledge", "quality", "question", "task", "resource", "assessment", "context", "standard"}
EVIDENCE_KINDS = {"quoted", "synthesized", "inferred", "proposed"}
REVIEW = {"unreviewed", "accepted", "rejected", "revised"}
REL_TYPES = {"requires_knowledge", "depends_on", "part_of", "develops_quality", "driven_by_question", "practiced_in", "supported_by_resource", "assessed_by", "derived_from", "parallel_to", "progresses_to", "includes"}


def validate_evidence(evidence, source_ids, path, errors):
    if not isinstance(evidence, list) or not evidence:
        errors.append(f"{path}.evidence must be a non-empty array")
        return
    for i, item in enumerate(evidence):
        p = f"{path}.evidence[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{p} must be an object")
            continue
        kind = item.get("evidence_kind")
        if kind not in EVIDENCE_KINDS:
            errors.append(f"{p}.evidence_kind invalid: {kind!r}")
        source_id = item.get("source_id")
        if kind != "proposed" and not source_id:
            errors.append(f"{p}.source_id required unless evidence_kind=proposed")
        if source_id and source_id not in source_ids:
            errors.append(f"{p}.source_id references unknown source: {source_id}")
        if kind == "quoted" and not item.get("quote"):
            errors.append(f"{p}.quote required for quoted evidence")
        if kind in {"synthesized", "inferred", "proposed"} and not item.get("reason"):
            errors.append(f"{p}.reason required for {kind} evidence")


def validate(data):
    errors, warnings = [], []
    for field in ("metadata", "sources", "nodes", "relations"):
        if field not in data:
            errors.append(f"missing top-level field: {field}")
    if errors:
        return errors, warnings, {}

    sources = data.get("sources", [])
    source_ids = set()
    for i, source in enumerate(sources):
        sid = source.get("id")
        if not sid:
            errors.append(f"sources[{i}].id missing")
        elif sid in source_ids:
            errors.append(f"duplicate source id: {sid}")
        else:
            source_ids.add(sid)

    all_ids, node_ids = set(), set()
    for i, node in enumerate(data.get("nodes", [])):
        path = f"nodes[{i}]"
        nid = node.get("id")
        if not nid:
            errors.append(f"{path}.id missing")
        elif nid in all_ids:
            errors.append(f"duplicate id: {nid}")
        else:
            all_ids.add(nid); node_ids.add(nid)
        if node.get("type") not in NODE_TYPES:
            errors.append(f"{path}.type invalid: {node.get('type')!r}")
        for field in ("name", "description"):
            if not isinstance(node.get(field), str) or not node[field].strip():
                errors.append(f"{path}.{field} must be a non-empty string")
        if node.get("review_status") not in REVIEW:
            errors.append(f"{path}.review_status invalid")
        validate_evidence(node.get("evidence"), source_ids, path, errors)
        parent = node.get("parent_id")
        if parent and parent == nid:
            errors.append(f"{path}.parent_id cannot reference itself")

    for i, rel in enumerate(data.get("relations", [])):
        path = f"relations[{i}]"
        rid = rel.get("id")
        if not rid:
            errors.append(f"{path}.id missing")
        elif rid in all_ids:
            errors.append(f"duplicate id: {rid}")
        else:
            all_ids.add(rid)
        if rel.get("type") not in REL_TYPES:
            errors.append(f"{path}.type invalid: {rel.get('type')!r}")
        for endpoint in ("from", "to"):
            value = rel.get(endpoint)
            if value not in node_ids:
                errors.append(f"{path}.{endpoint} references unknown node: {value!r}")
        if rel.get("from") == rel.get("to"):
            warnings.append(f"{path} is a self-relation")
        if rel.get("review_status") not in REVIEW:
            errors.append(f"{path}.review_status invalid")
        validate_evidence(rel.get("evidence"), source_ids, path, errors)

    for i, node in enumerate(data.get("nodes", [])):
        parent = node.get("parent_id")
        if parent and parent not in node_ids:
            errors.append(f"nodes[{i}].parent_id references unknown node: {parent}")
        if node.get("review_status") == "accepted" and any(e.get("evidence_kind") == "proposed" for e in node.get("evidence", [])):
            warnings.append(f"nodes[{i}] accepted although it contains proposed evidence; confirm review record")

    if data.get("metadata", {}).get("graph_status") in {"reviewed", "published"}:
        unreviewed = sum(1 for n in data.get("nodes", []) if n.get("review_status") == "unreviewed")
        if unreviewed:
            warnings.append(f"graph status is reviewed/published but {unreviewed} nodes remain unreviewed")
    
    # Connectivity analysis
    connectivity = analyze_connectivity(data.get("nodes", []), data.get("relations", []))
    
    # Root node warnings
    if connectivity["root_count"] == 0:
        warnings.append("⚠️  No root nodes found (all nodes have incoming edges)")
    elif connectivity["root_count"] > 1:
        root_ids = ", ".join(connectivity["root_ids"][:5])
        if len(connectivity["root_ids"]) > 5:
            root_ids += f" ... (+{len(connectivity['root_ids']) - 5} more)"
        warnings.append(f"⚠️  Multiple root nodes detected ({connectivity['root_count']}): {root_ids}")
        warnings.append("    Consider adding a Level 0 course goal node to unify the graph")
    
    # Isolated node warnings
    if connectivity["isolated_count"] > 0:
        isolated_ids = ", ".join(connectivity["isolated_ids"][:5])
        if len(connectivity["isolated_ids"]) > 5:
            isolated_ids += f" ... (+{len(connectivity['isolated_ids']) - 5} more)"
        warnings.append(f"⚠️  Isolated nodes found ({connectivity['isolated_count']}): {isolated_ids}")
        warnings.append("    These nodes have no incoming or outgoing edges")
    
    return errors, warnings, connectivity


def analyze_connectivity(nodes, relations):
    """Analyze graph connectivity: root nodes, isolated nodes, and connectivity stats."""
    node_ids = {n["id"] for n in nodes}
    
    # Find connected nodes
    has_incoming = set()
    has_outgoing = set()
    for rel in relations:
        source = rel.get("from")
        target = rel.get("to")
        if source in node_ids:
            has_outgoing.add(source)
        if target in node_ids:
            has_incoming.add(target)
    
    connected = has_incoming | has_outgoing
    
    # Find root nodes (no incoming edges)
    root_ids = [nid for nid in node_ids if nid not in has_incoming]
    
    # Find isolated nodes (no edges at all)
    isolated_ids = [nid for nid in node_ids if nid not in connected]
    
    # Find leaf nodes (no outgoing edges)
    leaf_ids = [nid for nid in node_ids if nid not in has_outgoing]
    
    return {
        "total_nodes": len(nodes),
        "total_relations": len(relations),
        "connected_nodes": len(connected),
        "root_count": len(root_ids),
        "root_ids": root_ids,
        "isolated_count": len(isolated_ids),
        "isolated_ids": isolated_ids,
        "leaf_count": len(leaf_ids),
        "connectivity_ratio": len(connected) / len(nodes) if nodes else 0
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate capability graph JSON")
    parser.add_argument("graph", type=Path)
    parser.add_argument("--schema", type=Path, help="optional JSON Schema; used if jsonschema is installed")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--connectivity", action="store_true", help="Show detailed connectivity analysis")
    args = parser.parse_args()
    data = json.loads(args.graph.read_text(encoding="utf-8"))
    errors, warnings, connectivity = validate(data)

    if args.schema:
        try:
            import jsonschema
            schema = json.loads(args.schema.read_text(encoding="utf-8"))
            for error in sorted(jsonschema.Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path)):
                location = ".".join(map(str, error.path)) or "$"
                errors.append(f"schema {location}: {error.message}")
        except ImportError:
            warnings.append("jsonschema package not installed; skipped formal schema validation")

    report = {"valid": not errors, "errors": errors, "warnings": warnings}
    
    if args.connectivity and connectivity:
        report["connectivity"] = connectivity
        print("\n📊 Connectivity Analysis:")
        print(f"  Total nodes: {connectivity['total_nodes']}")
        print(f"  Total relations: {connectivity['total_relations']}")
        print(f"  Connected nodes: {connectivity['connected_nodes']} ({connectivity['connectivity_ratio']:.1%})")
        print(f"  Root nodes: {connectivity['root_count']}")
        if connectivity['root_count'] > 0:
            print(f"    {', '.join(connectivity['root_ids'][:10])}")
        print(f"  Isolated nodes: {connectivity['isolated_count']}")
        if connectivity['isolated_count'] > 0:
            print(f"    {', '.join(connectivity['isolated_ids'][:10])}")
        print(f"  Leaf nodes: {connectivity['leaf_count']}")
        print()
    
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
