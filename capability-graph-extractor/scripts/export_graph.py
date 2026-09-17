#!/usr/bin/env python3
"""Export graph JSON to UTF-8 CSV tables and/or Mermaid flowchart."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


def flatten(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def write_csv(path: Path, rows: list[dict], preferred: list[str]):
    keys = list(preferred)
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: flatten(row.get(key)) for key in keys})


def mermaid_id(value: str) -> str:
    return "N_" + re.sub(r"[^A-Za-z0-9_]", "_", value)


def escape_label(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ")


def export_mermaid(path: Path, data: dict):
    nodes = {node["id"]: node for node in data.get("nodes", [])}
    lines = ["flowchart LR"]
    for node in nodes.values():
        label = escape_label(f"{node['name']}\\n[{node['type']}]")
        lines.append(f'  {mermaid_id(node["id"])}["{label}"]')
    for rel in data.get("relations", []):
        if rel.get("from") in nodes and rel.get("to") in nodes:
            label = escape_label(rel.get("type", ""))
            lines.append(f'  {mermaid_id(rel["from"])} -->|"{label}"| {mermaid_id(rel["to"])}')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export capability graph")
    parser.add_argument("graph", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--formats", default="csv,mermaid", help="csv,mermaid")
    args = parser.parse_args()
    formats = {item.strip().lower() for item in args.formats.split(",") if item.strip()}
    unknown = formats - {"csv", "mermaid"}
    if unknown:
        parser.error(f"unknown formats: {', '.join(sorted(unknown))}")

    data = json.loads(args.graph.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if "csv" in formats:
        write_csv(args.out_dir / "nodes.csv", data.get("nodes", []), ["id", "type", "name", "description", "level", "parent_id", "review_status"])
        write_csv(args.out_dir / "relations.csv", data.get("relations", []), ["id", "from", "to", "type", "description", "review_status"])
        write_csv(args.out_dir / "sources.csv", data.get("sources", []), ["id", "source_type", "title", "origin", "path_or_url", "read_status"])
    if "mermaid" in formats:
        export_mermaid(args.out_dir / "graph.mmd", data)
    print(f"Exported {','.join(sorted(formats))} -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
