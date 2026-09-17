#!/usr/bin/env python3
"""Assign deterministic IDs to graph nodes and relations that lack IDs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

PREFIX = {
    "capability": "CAP", "knowledge": "KNW", "quality": "QUA",
    "question": "QUE", "task": "TSK", "resource": "RES",
    "assessment": "ASM", "context": "CTX", "standard": "STD"
}


def slug(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10].upper()


def assign(data: dict) -> dict:
    used = {item.get("id") for key in ("nodes", "relations") for item in data.get(key, []) if item.get("id")}
    for node in data.get("nodes", []):
        if node.get("id"):
            continue
        prefix = PREFIX.get(node.get("type"), "NOD")
        base = f"{prefix}-{slug(node.get('name', '') + '|' + node.get('description', ''))}"
        node["id"] = unique(base, used)
        used.add(node["id"])

    for relation in data.get("relations", []):
        if relation.get("id"):
            continue
        base = "REL-" + slug("|".join([
            str(relation.get("from", "")), str(relation.get("type", "")),
            str(relation.get("to", "")), str(relation.get("description", ""))
        ]))
        relation["id"] = unique(base, used)
        used.add(relation["id"])
    return data


def unique(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    number = 2
    while f"{base}-{number}" in used:
        number += 1
    return f"{base}-{number}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Assign deterministic graph IDs")
    parser.add_argument("graph", type=Path)
    parser.add_argument("--output", "-o", type=Path)
    parser.add_argument("--write", action="store_true", help="overwrite input file")
    args = parser.parse_args()
    if args.write and args.output:
        parser.error("use either --write or --output")

    data = json.loads(args.graph.read_text(encoding="utf-8"))
    assign(data)
    target = args.graph if args.write else args.output
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"Assigned IDs -> {target}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
