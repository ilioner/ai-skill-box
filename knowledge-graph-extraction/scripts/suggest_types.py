#!/usr/bin/env python3
"""类型建议门禁（Agent 直抽版）：读取 Agent 提议、用户确认后的类型 JSON，固化进 type_presets.json。

智能体使用流程：
1. Agent 按 references/prompt-and-schema.md 的"类型建议模板"基于文档提议类型，
   展示给用户确认或修改；
2. 用户确认后，Agent 把类型写成 types.json（格式：{doc_type, level, extends, entity_types, relation_types}）；
3. 本脚本 --types-json types.json 打印建议（默认），--apply 固化进 type_presets.json。

门禁规则（本脚本机械强制 + SKILL.md 流程约束，双重保险）：
1. 抽取任何图谱数据前，必须先完成类型确认（本脚本固化或用户手填预设）；
2. 默认（无 --apply）只打印建议，绝不写盘；
3. --dry-run 也不写盘。

用法：
  python3 suggest_types.py --types-json types.json --preset-name my_course           # 打印建议
  python3 suggest_types.py --types-json types.json --preset-name my_course --apply   # 固化
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def load_presets(presets_file: str) -> dict[str, Any]:
    with open(presets_file, "r", encoding="utf-8") as fh:
        return json.load(fh)


def slug(name: str) -> str:
    out = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", name.strip()).strip("_").lower()
    return out or "course_auto"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="类型建议门禁（Agent 直抽版）：读取已确认类型 JSON，打印/固化到 type_presets.json")
    parser.add_argument("--types-json", required=True,
                        help="已确认的类型 JSON 文件（Agent 提议、用户确认后的产物），"
                             "格式：{doc_type, level, extends, entity_types, relation_types}")
    parser.add_argument("--course", default="", help="课程/文档集名称（用于预设 description 与 preset-name 缺省值）")
    parser.add_argument("--presets-file",
                        default=str(Path(__file__).resolve().parent.parent / "type_presets.json"),
                        help="类型预设文件路径（默认技能内置 type_presets.json）")
    parser.add_argument("--preset-name", default="", help="固化时新增预设的 key（缺省由 --course 生成）")
    parser.add_argument("--extends", default="",
                        help="固化时写入的继承基类（逗号分隔，缺省用类型文件里的 extends）")
    parser.add_argument("--dry-run", action="store_true", help="不写盘，仅打印将执行的步骤")
    parser.add_argument("--apply", action="store_true", help="确认后固化到 type_presets.json；不加则只打印建议")
    args = parser.parse_args()

    try:
        with open(args.types_json, "r", encoding="utf-8") as fh:
            suggestion = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: 读取类型文件失败: {exc}", file=sys.stderr)
        return 1
    if not isinstance(suggestion, dict) or not suggestion.get("entity_types"):
        print("error: 类型文件缺少 entity_types（格式：{doc_type, level, extends, entity_types, relation_types}）",
              file=sys.stderr)
        return 1

    presets_file = args.presets_file
    presets = load_presets(presets_file)
    base_names = sorted(k for k in presets.get("presets", {}) if k.endswith("_base") or k == "default")

    if args.dry_run:
        print(f"[dry-run] 将读取已确认类型 {args.types_json} 并打印建议；确认后加 --apply 固化到 {presets_file}")
        return 0

    level = str(suggestion.get("level") or "undergrad")
    doc_type = str(suggestion.get("doc_type") or "教材")
    extends = [str(x) for x in suggestion.get("extends") or []]
    entity_types = suggestion.get("entity_types") or []
    relation_types = suggestion.get("relation_types") or []

    # ── 打印建议 ──
    print(f"[建议] 文档集: {args.course or '(未指定)'}")
    print(f"[建议] 文档类型: {doc_type}")
    print(f"[建议] 学段: {level}")
    if extends:
        print(f"[建议] 继承基类: {', '.join(extends)}")
    print(f"[建议] 实体类型 ({len(entity_types)}):")
    for i, t in enumerate(entity_types, 1):
        name = t.get("name") if isinstance(t, dict) else str(t)
        reason = t.get("reason", "") if isinstance(t, dict) else ""
        print(f"  {i}. {name}" + (f" — {reason}" if reason else ""))
    print(f"[建议] 关系类型 ({len(relation_types)}):")
    for i, t in enumerate(relation_types, 1):
        name = t.get("name") if isinstance(t, dict) else str(t)
        reason = t.get("reason", "") if isinstance(t, dict) else ""
        print(f"  {i}. {name}" + (f" — {reason}" if reason else ""))

    if not args.apply:
        preset_name = args.preset_name or slug(args.course) or "course_auto"
        print(f"[plan] 未加 --apply，未写盘。确认无误后固化：")
        print(f"  python3 suggest_types.py --types-json {args.types_json} "
              f"--course \"{args.course}\" --preset-name {preset_name} "
              f"--extends {','.join(extends) or '<基类>'} --apply")
        print("[plan] 固化后抽取时使用 --type-preset <预设名>（Agent 抽取时读取该预设展开 Schema）")
        return 0

    # ── 固化 ──
    preset_name = args.preset_name or slug(args.course) or "course_auto"
    extends_final = [x.strip() for x in args.extends.split(",") if x.strip()] if args.extends else extends
    entry: dict[str, Any] = {
        "extends": extends_final,
        "level": level,
        "doc_type": doc_type,
        "description": f"课程预设：{args.course or preset_name}（已确认）",
        "entity_types": [t.get("name") if isinstance(t, dict) else str(t) for t in entity_types],
        "relation_types": [t.get("name") if isinstance(t, dict) else str(t) for t in relation_types],
    }
    presets.setdefault("presets", {})[preset_name] = entry
    with open(presets_file, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(presets, ensure_ascii=False, indent=2) + "\n")
    print(f"ok: 已固化预设 [{preset_name}] 到 {presets_file}", file=sys.stderr)
    print(f"[next] 抽取: Agent 按 references/prompt-and-schema.md 模板抽取，使用预设 {preset_name} 展开 Schema")
    print(f"[next] 同步: sync_graph_pg.py --mode <full|incremental> --graph graph.json ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
