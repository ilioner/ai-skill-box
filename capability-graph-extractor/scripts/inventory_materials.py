#!/usr/bin/env python3
"""Create a mechanical inventory of course-material files.

This script does not infer document authority or semantic roles. Those decisions
belong to the host model and human reviewers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

SUPPORTED = {
    ".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx", ".csv",
    ".txt", ".md", ".html", ".htm", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".bmp", ".mp3", ".wav", ".mp4", ".mov", ".json"
}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory course-material files")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument("--include-hidden", action="store_true")
    args = parser.parse_args()

    root = args.directory.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if not args.include_hidden and any(part.startswith(".") for part in rel.parts):
            continue
        stat = path.stat()
        suffix = path.suffix.lower()
        files.append({
            "path": rel.as_posix(),
            "extension": suffix,
            "mime_type": mimetypes.guess_type(path.name)[0],
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "sha256": sha256_file(path),
            "supported_extension": suffix in SUPPORTED,
            "suggested_role": None,
            "read_status": "not_read",
            "notes": []
        })

    output = {
        "root": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(files),
        "files": files,
        "notice": "This is a mechanical inventory. Document roles, authority and applicability require semantic review."
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Inventoried {len(files)} files -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
