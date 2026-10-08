#!/usr/bin/env python3
"""从源文件生成 learning_path.json（确定性，无模型参与）。

渲染脚本的「学习路径导览」面板吃这份数据：
  [{"unitId": "...", "name": "第一单元 …", "lessons": [{"lessonId": "...", "title": "…"}]}]

单元/课时 ID 来自每个源文件的 frontmatter（或 <textbookId>_<unitId>_<lessonId>.md 文件名）；
标题取文件里的 Markdown 标题，缺失则回退 ID。

用法：
  python3 build_learning_path.py --source-dir 豆包/lesson_mod --output 豆包/output/learning_path.json
  python3 build_learning_path.py --raws 豆包/output/raws.jsonl --output learning_path.json
  python3 build_learning_path.py --source-dir docs --dry-run      # 预览，不写盘
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_meta import extract_source_meta, parse_frontmatter  # noqa: E402

_H_RE = re.compile(r"^\s{0,3}(#{1,4})\s+(.+?)\s*#*\s*$")


def _strip_frontmatter(text: str) -> str:
    match = re.match(r"\A\ufeff?\s*---\s*\n.*?\n---\s*(?:\n|\Z)", text, re.S)
    return text[match.end():] if match else text


def _clean(text: str) -> str:
    """去掉富文本编辑器残片（{data-hash=...}）、首尾空白。"""
    text = re.sub(r'\{data-hash="[^"]*"\}', '', text)
    return text.strip()


def read_titles(path: Path) -> tuple[str, str]:
    """返回 (unit_title, lesson_title)：取文件内首个 H1（单元）+ 首个 H2（课时）。

    每个源文件视为一门课时：单元取顶层标题，课时取首个二级标题；二者都缺失
    则回退该文件首个任意标题；再缺失回退 ID。不做堆标题（避免「任务目标/学习目标」
    等小节被误列为课时）。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    meta = parse_frontmatter(text[:4096])
    body = _strip_frontmatter(text)

    h1 = h2 = None
    for line in body.splitlines():
        match = _H_RE.match(line)
        if not match:
            continue
        level, title = len(match.group(1)), _clean(match.group(2))
        if level == 1 and h1 is None:
            h1 = title
        elif level == 2 and h2 is None:
            h2 = title
        if h1 is not None and h2 is not None:
            break

    unit_title = _clean(str(meta.get("unitTitle") or meta.get("unitName") or "").strip()) \
        or h1 or ""
    lesson_title = _clean(str(meta.get("lessonTitle") or meta.get("title") or "").strip()) \
        or h2 or h1 or ""
    return unit_title, lesson_title


def collect_sources(args: argparse.Namespace) -> list[str]:
    """来源清单：--source-dir 扫描，或 --raws 读 raws.jsonl 的 source 字段。"""
    sources: list[str] = []
    if args.source_dir:
        root = Path(args.source_dir)
        for pattern in args.glob.split(","):
            pattern = pattern.strip()
            if pattern:
                sources.extend(str(p) for p in sorted(root.rglob(pattern)) if p.is_file())
    if args.raws:
        seen: set[str] = set()
        with open(args.raws, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                source = str(record.get("source") or "").strip().split("#", 1)[0]
                if source and source not in seen:
                    seen.add(source)
                    sources.append(source)
    return sources


def build_path(sources: list[str], source_root: str | None) -> list[dict[str, Any]]:
    """按 unitId 分组、lessonId 去重，保持首次出现顺序。"""
    units: dict[str, dict[str, Any]] = {}
    for source in sources:
        meta = extract_source_meta(source, source_root, ("textbookId", "unitId", "lessonId"))
        unit_id = str(meta.get("unitId") or "").strip()
        lesson_id = str(meta.get("lessonId") or "").strip()
        if not unit_id and not lesson_id:
            continue

        candidate = Path(source)
        if source_root and not candidate.is_absolute():
            candidate = Path(source_root) / candidate
        unit_title, lesson_title = read_titles(candidate)
        if not lesson_title:
            lesson_title = unit_title

        unit = units.setdefault(unit_id or lesson_id, {
            "unitId": unit_id or lesson_id,
            "name": unit_title or f"单元 {unit_id or lesson_id}",
            "lessons": [],
            "_lesson_ids": set(),
        })
        # 单元名：首个非空标题胜出（避免被后续课时文件的标题覆盖）
        if unit["name"].startswith("单元 ") and unit_title:
            unit["name"] = unit_title

        if lesson_id and lesson_id not in unit["_lesson_ids"]:
            unit["_lesson_ids"].add(lesson_id)
            unit["lessons"].append({
                "lessonId": lesson_id,
                "title": lesson_title or unit_title or f"课时 {lesson_id}",
            })

    result = []
    for unit in units.values():
        unit.pop("_lesson_ids", None)
        result.append(unit)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 learning_path.json（学习路径导览数据）")
    ap.add_argument("--source-dir", default="", help="源文件目录（递归扫描）")
    ap.add_argument("--glob", default="*.md,*.markdown", help="源文件匹配模式（逗号分隔，默认 *.md,*.markdown）")
    ap.add_argument("--raws", default="", help="从 raws.jsonl 的 source 字段取来源清单")
    ap.add_argument("--source-root", default="", help="source 相对路径的解析基准目录")
    ap.add_argument("--output", default="", help="输出 learning_path.json；缺省写 stdout")
    ap.add_argument("--dry-run", action="store_true", help="只预览统计，不写盘")
    args = ap.parse_args()

    if not args.source_dir and not args.raws:
        print("error: 需要 --source-dir 或 --raws", file=sys.stderr)
        return 1

    sources = collect_sources(args)
    if not sources:
        print("error: 未找到任何源文件", file=sys.stderr)
        return 1

    path_data = build_path(sources, args.source_root or None)
    if not path_data:
        print(f"error: {len(sources)} 个来源中未提取到 unitId/lessonId"
              "（检查 frontmatter 或文件名格式）", file=sys.stderr)
        return 1

    lesson_total = sum(len(u["lessons"]) for u in path_data)
    summary = f"{len(sources)} sources -> {len(path_data)} units, {lesson_total} lessons"

    payload = json.dumps(path_data, ensure_ascii=False, indent=2)
    if args.dry_run:
        print(f"dry-run: {summary}（未写盘）", file=sys.stderr)
        print(payload)
        return 0
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(f"ok: {summary} -> {args.output}", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
