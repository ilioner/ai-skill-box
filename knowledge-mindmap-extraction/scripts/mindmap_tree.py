#!/usr/bin/env python3
"""知识导图树工具：校验、叶子收集、删除剪枝、变更检测。

树结构（与 markmap / 前端通用）：
  {"content": "根节点", "children": [{"content": "...", "children": []}, ...]}

用法：
  python3 mindmap_tree.py --mode validate --input mindmap.json
  python3 mindmap_tree.py --mode diff --mindmap mindmap.json --tracked tracked.json --files files.jsonl
  python3 mindmap_tree.py --mode prune --input mindmap.json --remove 文件A.pdf,文件B.md --output pruned.json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


# ─── 基础操作 ───────────────────────────────────────────────


def collect_leaf_filenames(node: dict[str, Any]) -> set[str]:
    """递归收集所有叶子节点的 content（叶子 = children 为空）。"""
    children = node.get("children") or []
    if not children:
        content = str(node.get("content") or "").strip()
        return {content} if content else set()
    result: set[str] = set()
    for child in children:
        result |= collect_leaf_filenames(child)
    return result


def validate(tree: dict[str, Any]) -> list[str]:
    """校验树结构，返回错误列表（空列表 = 合法）。"""
    errors: list[str] = []

    def walk(node: dict[str, Any], path: str) -> None:
        if not isinstance(node, dict):
            errors.append(f"{path}: 节点必须是对象")
            return
        if not str(node.get("content") or "").strip():
            errors.append(f"{path}: content 不能为空")
        children = node.get("children")
        if children is None:
            errors.append(f"{path}: 缺少 children 字段")
            return
        if not isinstance(children, list):
            errors.append(f"{path}: children 必须是数组")
            return
        if not children:
            return
        for index, child in enumerate(children):
            walk(child, f"{path}.children[{index}]")

    walk(tree, "root")
    # 叶子文件名唯一性（同级与跨级都不允许重复）
    leaves = collect_leaf_filenames(tree)
    if len(leaves) != sum(1 for _ in _walk_leaves(tree)):
        errors.append("叶子节点存在重复文件名")
    return errors


def _walk_leaves(node: dict[str, Any]):
    children = node.get("children") or []
    if not children:
        yield node
        return
    for child in children:
        yield from _walk_leaves(child)


def remove_files(tree: dict[str, Any], removed_filenames: set[str]) -> dict[str, Any] | None:
    """递归剪枝：移除指定文件名的叶子节点（纯树手术，无 AI 调用）。"""
    if not removed_filenames:
        return tree
    content = str(tree.get("content") or "")
    children = tree.get("children") or []

    if not children:
        return None if content in removed_filenames else tree

    pruned = []
    for child in children:
        result = remove_files(child, removed_filenames)
        if result is not None:
            pruned.append(result)

    node = {"content": content, "children": pruned}
    if not pruned:
        return None
    return node


# ─── 变更检测 ───────────────────────────────────────────────


def detect_changes(
    mindmap: dict[str, Any] | None,
    tracked_file_ids: dict[str, str] | None,
    current_files: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """对比导图追踪的文件与知识库当前文件，返回变更信息。

    Args:
        mindmap: 现有导图树（可为 None）
        tracked_file_ids: 导图追踪的 {file_id: filename}（可为 None）
        current_files: 当前文件 {file_id: {filename, type, ...}}

    Returns:
        {added_files, removed_file_ids, unchanged_count, needs_update}
    """
    if mindmap and not tracked_file_ids:
        # 兼容旧数据：通过叶子文件名反向重建映射
        leaf_filenames = collect_leaf_filenames(mindmap)
        tracked_file_ids = {
            fid: info.get("filename", "")
            for fid, info in current_files.items()
            if info.get("filename", "") in leaf_filenames
        }

    if not mindmap or not tracked_file_ids:
        added_files = [
            {"file_id": fid, "filename": info.get("filename", ""), "type": info.get("type", "")}
            for fid, info in current_files.items()
        ]
        return {
            "has_mindmap": mindmap is not None,
            "added_files": added_files,
            "removed_file_ids": [],
            "unchanged_count": 0,
            "needs_update": len(added_files) > 0,
        }

    tracked_ids = set(tracked_file_ids.keys())
    current_ids = set(current_files.keys())
    removed_file_ids = sorted(tracked_ids - current_ids)
    added_file_ids = sorted(current_ids - tracked_ids)
    added_files = [
        {"file_id": fid, "filename": current_files[fid].get("filename", ""), "type": current_files[fid].get("type", "")}
        for fid in added_file_ids
        if fid in current_files
    ]
    return {
        "has_mindmap": True,
        "added_files": added_files,
        "removed_file_ids": removed_file_ids,
        "unchanged_count": len(tracked_ids & current_ids),
        "needs_update": bool(added_files) or bool(removed_file_ids),
    }


# ─── 文件清单 ───────────────────────────────────────────────


def load_files(path: str | None) -> list[dict[str, str]]:
    """读取文件清单：JSON 数组 / JSONL（每行含 id|file_id, filename, type）。"""
    if not path:
        return []
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    records: list[dict[str, Any]]
    if path.endswith(".jsonl"):
        records = [json.loads(line) for line in content.splitlines() if line.strip()]
    else:
        records = json.loads(content)
    files = []
    for record in records:
        fid = str(record.get("id") or record.get("file_id") or "")
        filename = str(record.get("filename") or "").strip()
        if not filename:
            continue
        files.append({"file_id": fid, "filename": filename, "type": str(record.get("type") or "")})
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="知识导图树工具")
    parser.add_argument("--mode", choices=["validate", "diff", "prune"], required=True)
    parser.add_argument("--input", help="导图 JSON 文件（validate/prune 用）")
    parser.add_argument("--mindmap", help="现有导图 JSON（diff 用）")
    parser.add_argument("--tracked", help="追踪文件 JSON：{file_id: filename}（diff 用）")
    parser.add_argument("--files", help="当前文件清单 JSON/JSONL（diff 用）")
    parser.add_argument("--remove", help="要删除的文件名，逗号分隔（prune 用）")
    parser.add_argument("--output", help="输出 JSON 文件（prune 用）")
    args = parser.parse_args()

    if args.mode == "validate":
        with open(args.input, "r", encoding="utf-8") as fh:
            tree = json.load(fh)
        errors = validate(tree)
        if errors:
            print("invalid:")
            for error in errors:
                print(f"  - {error}")
            return 1
        print(f"ok: 树合法，{len(collect_leaf_filenames(tree))} 个叶子节点")
        return 0

    if args.mode == "diff":
        mindmap = json.load(open(args.mindmap, "r", encoding="utf-8")) if args.mindmap else None
        tracked = json.load(open(args.tracked, "r", encoding="utf-8")) if args.tracked else None
        files = load_files(args.files)
        current = {f["file_id"] or f["filename"]: f for f in files}
        changes = detect_changes(mindmap, tracked, current)
        print(json.dumps(changes, ensure_ascii=False, indent=2))
        return 0

    if args.mode == "prune":
        with open(args.input, "r", encoding="utf-8") as fh:
            tree = json.load(fh)
        removed = {name.strip() for name in args.remove.split(",") if name.strip()}
        pruned = remove_files(tree, removed)
        if pruned is None:
            pruned = {"content": str(tree.get("content") or ""), "children": []}
        payload = json.dumps(pruned, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(payload + "\n")
        else:
            print(payload)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
