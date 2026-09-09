#!/usr/bin/env python3
"""类型建议门禁：抽取前，基于模型对文档集抽样提议节点/关系类型，用户确认后固化。

门禁规则（本脚本机械强制 + SKILL.md 流程约束，双重保险）：
1. 抽取任何图谱数据前，必须先运行本脚本让模型基于文档内容提议类型；
2. 默认（无 --apply）只打印建议，绝不写任何文件；
3. --apply 才把用户确认后的类型固化进 type_presets.json；
4. --dry-run 不调模型、不写盘（用于无 API Key 环境验证 CLI 与流程）。

用法：
  # 先看建议（不写盘）
  python3 suggest_types.py --files-dir docs/ --course "民航安检服务" \
      --api-key $OPENAI_API_KEY

  # 确认后固化（写 type_presets.json 新增一条课程预设）
  python3 suggest_types.py --files-dir docs/ --course "民航安检服务" \
      --preset-name civil_aviation_security \
      --extends vocational_base,professional_base \
      --api-key $OPENAI_API_KEY --apply

  # 无 Key 环境验证 CLI
  python3 suggest_types.py --files-dir docs/ --dry-run

环境变量：OPENAI_API_KEY / OPENAI_BASE_URL（默认 https://api.openai.com/v1）/
GRAPH_EXTRACTION_MODEL（默认 gpt-4o-mini）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".jsonl", ".html", ".htm", ".csv", ".rst", ".tex", ".docx"}

SUGGEST_PROMPT = """你是知识图谱类型体系设计专家。下面是文档集《{course}》的抽样内容（{sample_note}）。
请按以下步骤为该文档集设计知识图谱的「实体类型」和「关系类型」：

第一步：判断文档类型——从 教材 / 法规 / 技术文档 / 规章制度 中选一个最贴切的；
第二步：判断学段——从 k12（基础教育）/ undergrad（本科）/ vocational（职业教育）中选一个；
第三步：基于抽样内容设计类型。

背景：
- 文档集目录/文件名线索：{dirs}
- 学段：{level_hint}
- 可继承的内置基类（extends 候选，从中选择 0~3 个）：{base_names}

要求：
- 实体类型 8~15 个，关系类型 6~10 个；
- 类型要贴合该文档集的实际内容（文档类型、学段、课程主题），能覆盖主要知识单元，类型之间尽量正交、不重叠；
- 输出严格 JSON（不要输出解释）：
{{
  "doc_type": "教材|法规|技术文档|规章制度",
  "level": "k12|undergrad|vocational",
  "extends": ["基类名1", "基类名2"],
  "entity_types": [{{"name": "类型名", "reason": "一句话理由"}}],
  "relation_types": [{{"name": "类型名", "reason": "一句话理由"}}]
}}

抽样内容开始：
{sample}
抽样内容结束。
"""


def chat_completion(messages: list[dict[str, str]], api_base: str, api_key: str, model: str,
                    timeout: int = 120) -> str:
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": 0.2, "stream": False}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"模型响应格式异常: {exc}, body={str(data)[:300]}") from exc


def parse_json_response(content: str) -> dict[str, Any]:
    if not content:
        raise ValueError("模型返回空内容")
    text = content.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法解析模型输出为 JSON: {text[:200]}")


def scan_text_files(directory: str) -> list[str]:
    files = []
    for fp in sorted(Path(directory).rglob("*")):
        if fp.is_file() and fp.suffix.lower() in TEXT_EXTENSIONS:
            files.append(str(fp))
    return files


def load_files_json(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    records: list[dict[str, Any]]
    if path.endswith(".jsonl"):
        records = [json.loads(line) for line in content.splitlines() if line.strip()]
    else:
        records = json.loads(content)
    paths = []
    for record in records:
        p = str(record.get("path") or record.get("filename") or "")
        if p:
            paths.append(p)
    return paths


def sample_documents(files: list[str], max_files: int, chars_per_file: int, total_cap: int = 8000) -> str:
    parts: list[str] = []
    budget = total_cap
    for fp in files[:max_files]:
        try:
            text = Path(fp).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        snippet = re.sub(r"\s+", " ", text).strip()[:chars_per_file]
        if not snippet:
            continue
        head = f"--- {fp} ---\n{snippet}"
        if len(head) > budget:
            head = head[:budget]
        parts.append(head)
        budget -= len(head)
        if budget <= 0:
            break
    return "\n\n".join(parts) or "(文档集无可读文本)"


def load_presets(presets_file: str) -> dict[str, Any]:
    with open(presets_file, "r", encoding="utf-8") as fh:
        return json.load(fh)


def slug(name: str) -> str:
    out = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", name.strip()).strip("_").lower()
    return out or "course_auto"


def main() -> int:
    parser = argparse.ArgumentParser(description="类型建议门禁：模型提议节点/关系类型，用户确认后固化")
    parser.add_argument("--files-dir", help="扫描目录（文本文件）")
    parser.add_argument("--files-json", help="文件清单 JSON/JSONL：[{path|filename, ...}]")
    parser.add_argument("--input", help="单个文件或目录")
    parser.add_argument("--course", default="", help="课程/文档集名称（帮助模型定位）")
    parser.add_argument("--level", default="auto", choices=["auto", "k12", "undergrad", "vocational"],
                        help="学段提示；auto=由模型根据内容判断（默认）")
    parser.add_argument("--model", default=os.getenv("GRAPH_EXTRACTION_MODEL") or "gpt-4o-mini")
    parser.add_argument("--api-base", default=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1")
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--presets-file",
                        default=str(Path(__file__).resolve().parent.parent / "type_presets.json"),
                        help="类型预设文件路径（默认技能内置 type_presets.json）")
    parser.add_argument("--preset-name", default="", help="固化时新增预设的 key（缺省由 --course 生成）")
    parser.add_argument("--extends", default="", help="固化时写入的继承基类（逗号分隔，缺省用模型建议）")
    parser.add_argument("--sample-files", type=int, default=5, help="抽样文件数（默认 5）")
    parser.add_argument("--sample-chars", type=int, default=1500, help="每个文件抽样字符数（默认 1500）")
    parser.add_argument("--dry-run", action="store_true", help="不调模型、不写盘，仅打印将执行的步骤")
    parser.add_argument("--apply", action="store_true", help="确认后固化到 type_presets.json；不加则只打印建议")
    args = parser.parse_args()

    # ── 收集文件清单 ──
    if args.files_dir:
        files = scan_text_files(args.files_dir)
    elif args.files_json:
        files = load_files_json(args.files_json)
    elif args.input:
        p = Path(args.input)
        files = scan_text_files(str(p)) if p.is_dir() else [str(p)]
    else:
        print("error: 需要 --files-dir / --files-json / --input 之一", file=sys.stderr)
        return 1
    files = [f for f in files if Path(f).is_file()]
    if not files:
        print("error: 没有可读文本文件", file=sys.stderr)
        return 1

    presets_file = args.presets_file
    presets = load_presets(presets_file)
    base_names = sorted(k for k in presets.get("presets", {}) if k.endswith("_base") or k == "default")

    if args.dry_run:
        print(f"[dry-run] 文档集文件数={len(files)}，抽样 {min(args.sample_files, len(files))} 个文件"
              f"（每文件前 {args.sample_chars} 字符）")
        print(f"[dry-run] 将调用模型 {args.model} 提议类型（course={args.course or '(未指定)'}, "
              f"level={args.level}），并打印建议供确认")
        print(f"[dry-run] 确认后加 --apply 固化到 {presets_file}（preset-name="
              f"{args.preset_name or slug(args.course) or 'course_auto'}）")
        return 0

    if not args.api_key:
        print("error: 需要 --api-key 或环境变量 OPENAI_API_KEY", file=sys.stderr)
        return 1

    # ── 抽样 + 提议 ──
    sample = sample_documents(files, args.sample_files, args.sample_chars)
    dirs_hint = ", ".join(str(Path(f).name) for f in files[:20]) or "(无)"
    level_hint = {"auto": "auto（请根据内容判断）", "k12": "k12 基础教育",
                  "undergrad": "undergrad 本科", "vocational": "vocational 职业教育"}[args.level]
    prompt = SUGGEST_PROMPT.format(
        course=args.course or Path(files[0]).parent.name or "未命名文档集",
        sample_note=f"共 {len(files)} 个文件，抽样 {min(args.sample_files, len(files))} 个",
        dirs=dirs_hint,
        level_hint=level_hint,
        base_names=", ".join(base_names),
        sample=sample,
    )
    print(f"info: 抽样 {len(files[:args.sample_files])} 个文件，调用模型 {args.model} 提议类型…", file=sys.stderr)
    messages = [
        {"role": "system", "content": "你是知识图谱类型体系设计专家，只输出 JSON。"},
        {"role": "user", "content": prompt},
    ]
    try:
        raw = chat_completion(messages, args.api_base, args.api_key, args.model)
        suggestion = parse_json_response(raw)
    except Exception as exc:  # noqa: BLE001
        print(f"error: 类型提议失败: {exc}", file=sys.stderr)
        return 1

    level = str(suggestion.get("level") or args.level or "undergrad")
    if level == "auto":
        level = "undergrad"
    doc_type = str(suggestion.get("doc_type") or "教材")
    extends = [x.strip() for x in str(suggestion.get("extends") or "").split(",") if x.strip()] \
        if isinstance(suggestion.get("extends"), str) else [str(x) for x in suggestion.get("extends") or []]
    entity_types = suggestion.get("entity_types") or []
    relation_types = suggestion.get("relation_types") or []
    if not entity_types:
        print("error: 模型未返回实体类型，建议重试", file=sys.stderr)
        return 1

    # ── 打印建议 ──
    print(f"[建议] 文档集: {args.course or Path(files[0]).parent.name}（{len(files)} 个文件）")
    print(f"[建议] 文档类型: {doc_type}")
    print(f"[建议] 推断学段: {level}")
    if extends:
        print(f"[建议] 建议继承基类: {', '.join(extends)}")
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
        print(f"  python3 suggest_types.py --files-dir {args.files_dir or ''} "
              f"--course \"{args.course}\" --preset-name {preset_name} "
              f"--extends {','.join(extends) or '<基类>'} --apply")
        print("[plan] 固化后抽取时使用 --type-preset <预设名>（或 --type-map 按文件路由）")
        return 0

    # ── 固化 ──
    preset_name = args.preset_name or slug(args.course) or "course_auto"
    extends_final = [x.strip() for x in args.extends.split(",") if x.strip()] if args.extends else extends
    entry: dict[str, Any] = {
        "extends": extends_final,
        "level": level,
        "doc_type": doc_type,
        "description": f"课程预设：{args.course or preset_name}（由 suggest_types.py 生成，已确认）",
        "entity_types": [t.get("name") if isinstance(t, dict) else str(t) for t in entity_types],
        "relation_types": [t.get("name") if isinstance(t, dict) else str(t) for t in relation_types],
    }
    presets.setdefault("presets", {})[preset_name] = entry
    with open(presets_file, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(presets, ensure_ascii=False, indent=2) + "\n")
    print(f"ok: 已固化预设 [{preset_name}] 到 {presets_file}", file=sys.stderr)
    print(f"[next] 抽取: extract_graph.py --input ... --type-preset {preset_name}")
    print(f"[next] 同步: sync_graph_pg.py --mode <full|incremental> ... --type-preset {preset_name} "
          f"（或把路由加进 type_presets.json 的 routes 后用 --type-map）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
