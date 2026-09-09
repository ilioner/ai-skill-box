#!/usr/bin/env python3
"""生成 / 增量维护知识导图 JSON 树（Agent 直抽版 · 无 LLM、无 api-key）。

智能体使用流程：
1. 门禁问"新增 or 全量"（--mode 必填）；
2. Agent 读取 references/prompts.md 模板，基于文件清单生成/整合导图树，产出完整树 JSON（--tree-json）：
   - full：基于全部文件生成 2-4 层树（根=库名，一级=分类，叶子=文件名且全树唯一）
   - incremental：保留现有结构，对新增文件做增量整合、对删除做剪枝
3. 本脚本 --tree-json tree.json 只做确定性工作：校验、写 mindmap.json + tracked.json。

用法：
  # 全量（先问用户确认"全量"）
  python3 generate_mindmap.py --mode full --tree-json tree.json \
      --files-dir docs/ --output mindmap.json --apply

  # 增量（先问用户确认"新增"；Agent 提供整合后的完整树）
  python3 generate_mindmap.py --mode incremental --tree-json tree_inc.json \
      --existing mindmap.json --tracked tracked.json \
      --files-dir docs/ --output mindmap.json [--apply]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mindmap_tree import (  # noqa: E402
    collect_leaf_filenames,
    detect_changes,
    load_files,
    validate,
)

DEFAULT_MAX_FILES = 200


# ─── 输入 ───────────────────────────────────────────────────


def scan_directory(path: str, limit: int) -> list[dict[str, str]]:
    files = []
    for fp in sorted(Path(path).rglob("*")):
        if not fp.is_file():
            continue
        relative = str(fp.relative_to(path))
        if relative.startswith("."):
            continue
        files.append({"file_id": relative, "filename": fp.name, "type": fp.suffix.lstrip(".")})
        if len(files) >= limit:
            break
    return files


def load_tree(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        tree = json.load(fh)
    if not isinstance(tree, dict) or "content" not in tree or "children" not in tree:
        raise ValueError("导图结构不正确：缺少 content 或 children")
    return tree


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成 / 增量维护知识导图 JSON 树（Agent 直抽版：输入 --tree-json；门禁 --mode 必填、--apply 才写文件）")
    parser.add_argument("--files-dir", help="扫描目录下的文件名（递归）")
    parser.add_argument("--files-json", help="文件清单 JSON/JSONL：[{id|file_id, filename, type}]")
    parser.add_argument("--tree-json", required=True,
                        help="Agent 生成的完整导图树 JSON（{content, children}）")
    parser.add_argument("--mode", required=True, choices=["full", "incremental"],
                        help="full=全量重建；incremental=增量更新。运行前必须先与用户确认模式")
    parser.add_argument("--existing", help="现有导图 JSON（incremental 必需，用于 diff 显示）")
    parser.add_argument("--tracked", help="追踪文件 JSON：{file_id: filename}（incremental）")
    parser.add_argument("--output", default="mindmap.json", help="输出导图 JSON（默认 mindmap.json）")
    parser.add_argument("--tracked-output", default="tracked.json", help="输出追踪映射（默认 tracked.json）")
    parser.add_argument("--root-name", default="知识库", help="根节点名称")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help=f"文件数上限（默认 {DEFAULT_MAX_FILES}）")
    parser.add_argument("--apply", action="store_true", help="执行并写文件；不加则只打印计划")
    args = parser.parse_args()

    # 读取文件清单
    if args.files_dir:
        files_info = scan_directory(args.files_dir, args.max_files)
    elif args.files_json:
        files_info = load_files(args.files_json)
        if len(files_info) > args.max_files:
            files_info = files_info[: args.max_files]
    else:
        print("error: 需要 --files-dir 或 --files-json", file=sys.stderr)
        return 1
    if not files_info:
        print("error: 文件清单为空", file=sys.stderr)
        return 1
    current_files = {f["file_id"] or f["filename"]: f for f in files_info}

    # ── 门禁：模式由 --mode 强制；incremental 必须提供现有导图 ──
    if args.mode == "incremental" and not args.existing:
        print("error: 增量模式需要 --existing（现有导图 JSON）。首次生成请用 --mode full。", file=sys.stderr)
        return 1

    if args.mode == "incremental":
        with open(args.existing, "r", encoding="utf-8") as fh:
            mindmap = json.load(fh)
        tracked = json.load(open(args.tracked, "r", encoding="utf-8")) if args.tracked and Path(args.tracked).exists() else None
        changes = detect_changes(mindmap, tracked, current_files)
        print(f"[plan] kb={args.root_name} mode=incremental 当前文件数={len(current_files)}", file=sys.stderr)
        print(f"[plan] 新增 {len(changes['added_files'])} 个文件: "
              f"{', '.join(f['filename'] for f in changes['added_files'][:20]) or '无'}"
              f"{' …' if len(changes['added_files']) > 20 else ''}", file=sys.stderr)
        print(f"[plan] 删除 {len(changes['removed_file_ids'])} 个文件", file=sys.stderr)
        if not changes["needs_update"]:
            print("[plan] 无变更，导图已是最新，无需执行。")
            return 0
        if not args.apply:
            print("[plan] 未加 --apply，仅打印计划，不写文件。确认后加 --apply 执行。", file=sys.stderr)
            return 0
        try:
            mindmap = load_tree(args.tree_json)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"error: 读取导图失败: {exc}", file=sys.stderr)
            return 1
        tracked = {fid: info.get("filename", "") for fid, info in current_files.items()}
        print(f"[plan] 使用 Agent 提供的树 {args.tree_json}（跳过 LLM）", file=sys.stderr)
        metadata = {
            "generated_at": datetime.now(UTC).isoformat(),
            "file_count": len(tracked),
            "incremental": True,
        }
    else:
        # 全量模式
        overwrite_note = "（将覆盖现有 mindmap.json / tracked.json）" if args.existing and Path(args.existing).exists() else ""
        print(f"[plan] kb={args.root_name} mode=full 将全量重建导图：{len(files_info)} 个文件 {overwrite_note}",
              file=sys.stderr)
        if not args.apply:
            print("[plan] 未加 --apply，仅打印计划，不写文件。确认后加 --apply 执行。", file=sys.stderr)
            return 0
        try:
            mindmap = load_tree(args.tree_json)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"error: 读取导图失败: {exc}", file=sys.stderr)
            return 1
        tracked = {f["file_id"] or f["filename"]: f["filename"] for f in files_info}
        print(f"[plan] 使用 Agent 提供的树 {args.tree_json}（跳过 LLM）", file=sys.stderr)
        metadata = {
            "generated_at": datetime.now(UTC).isoformat(),
            "file_count": len(tracked),
            "incremental": False,
        }

    errors = validate(mindmap)
    if errors:
        print("warn: 生成树未通过校验：", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)

    Path(args.output).write_text(json.dumps(mindmap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.tracked_output).write_text(json.dumps(tracked or {}, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    leaf_count = len(collect_leaf_filenames(mindmap))
    print(f"ok: {len(files_info)} 个文件 -> 导图 {leaf_count} 个叶子节点 -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
