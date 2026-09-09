#!/usr/bin/env python3
"""从文本/文档块中抽取知识图谱数据（实体-关系三元组）。

工作流：
1. 读取输入（文本文件 / 目录 / JSONL chunks / JSON 数组 / stdin）
2. 按 max-chars 分块（优先段落边界）
3. 并发调用 OpenAI 兼容 Chat Completions API 抽取实体与关系
4. 容错解析（剥代码块 -> JSON -> 可选 json_repair）
5. 规范化去重合并，跨块合并实体与关系，输出标准化图谱 JSON

用法：
  python3 extract_graph.py --input docs/ --output graph.json \
      --model gpt-4o-mini --api-key $OPENAI_API_KEY [--schema "实体类型: 法规/机构/产品"]
  cat notes.txt | python3 extract_graph.py --stdin --output graph.json
  python3 extract_graph.py --input chunks.jsonl --output graph.json --id-field chunk_id --content-field content

环境变量：OPENAI_API_KEY / OPENAI_BASE_URL（默认 https://api.openai.com/v1）/ GRAPH_EXTRACTION_MODEL
可选依赖：json_repair（未安装时使用内置容错解析）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_graph import normalize_entity_name, normalize_extraction_result  # noqa: E402

DEFAULT_PROMPT = """请从下面文本中抽取实体和实体关系，返回严格 JSON，不要输出解释。
JSON 格式：
{
  "relations": [
    {
      "source": {"text": "实体文本", "label": "实体类型", "description": "原文摘录", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "target": {"text": "实体文本", "label": "实体类型", "description": "原文摘录", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "text": "关系显示文本",
      "label": "关系类型"
    }
  ]
}

description 字段规则（重要）：
- 必须从输入文本中逐字摘录"描述该实体"的原句或原文片段，不得总结、不得改写、不得扩写；
- 允许截取原句的一部分，允许用省略号表示省略，但截取内容必须与原文逐字一致；
- 若输入文本中没有直接描述该实体的原句，description 填空字符串 ""；
- 不要为了凑描述而编造或拼凑原文之外的文字。
"""

SCHEMA_INSTRUCTION = """抽取 Schema 约束：
{schema}
"""

TYPE_MODE_INSTRUCTION = {
    "strict": "\n严格模式：只允许使用上述 Schema 中的实体/关系类型，不得新增其他类型；无法归入任何类型的内容可以省略不抽。",
    "loose": "\n宽松模式：以上述 Schema 类型为主；确有必要时可少量补充新类型（尽量少，且补充类型名要规范统一）。",
}

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".jsonl", ".html", ".htm", ".csv", ".rst"}


# ─── 输入读取 ───────────────────────────────────────────────


def read_input(path: str | None) -> list[dict[str, Any]]:
    """返回 chunk 列表：[{content, chunk_id?, ...}]。"""
    if path is None:
        content = sys.stdin.read()
        return [{"content": content, "chunk_id": "stdin"}]
    p = Path(path)
    if p.is_dir():
        chunks = []
        for fp in sorted(p.rglob("*")):
            if fp.is_file() and fp.suffix.lower() in TEXT_EXTENSIONS:
                try:
                    text = fp.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if text.strip():
                    chunks.append({"content": text, "chunk_id": str(fp)})
        if not chunks:
            raise SystemExit(f"error: 目录 {path} 中没有可读文本文件")
        return chunks
    if p.suffix.lower() in {".jsonl"}:
        chunks = []
        with open(p, "r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("content"):
                    chunks.append(record)
        if not chunks:
            raise SystemExit(f"error: {path} 中没有有效 JSONL 行（需含 content 字段）")
        return chunks
    if p.suffix.lower() == ".json":
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("content"):
            return data
        raise SystemExit("error: JSON 文件需为 [{content, chunk_id?, ...}, ...] 数组")
    text = p.read_text(encoding="utf-8")
    return [{"content": text, "chunk_id": p.name}]


def split_chunks(records: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """按 max_chars 切分长文本，优先在段落边界断开。

    分块命名：第一个块沿用原 chunk_id，后续块为 "{chunk_id}#part2/3/..."，
    保证文件级来源可用前缀匹配（file_id 或 file_id#partN）。
    """
    chunks: list[dict[str, Any]] = []
    for record in records:
        content = record.get("content") or ""
        base = {k: v for k, v in record.items() if k != "content"}
        chunk_id = str(base.get("chunk_id") or "chunk")
        if len(content) <= max_chars:
            chunks.append({**base, "content": content})
            continue
        paragraphs = re.split(r"\n\s*\n", content)
        buf = ""
        part_no = 1
        for para in paragraphs:
            if not para.strip():
                continue
            if buf and len(buf) + len(para) + 2 > max_chars:
                part_no += 1
                chunks.append({**base, "chunk_id": f"{chunk_id}#part{part_no - 1}", "content": buf.strip()})
                buf = ""
            buf += ("\n\n" if buf else "") + para
        if buf.strip():
            part_no += 1
            chunks.append({**base, "chunk_id": f"{chunk_id}#part{part_no - 1}", "content": buf.strip()})
    return [c for c in chunks if c.get("content", "").strip()]


# ─── LLM 调用 ──────────────────────────────────────────────


def chat_completion(messages: list[dict[str, str]], api_base: str, api_key: str, model: str,
                    temperature: float = 0.0, timeout: int = 120) -> str:
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"模型响应格式异常: {exc}, body={str(data)[:300]}") from exc


def parse_json_response(content: str) -> dict[str, Any]:
    """容错解析：剥代码块 -> json.loads -> json_repair -> 宽松提取。"""
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
    # 提取最外层大括号
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    try:
        import json_repair  # type: ignore

        return json_repair.loads(text)
    except Exception:
        pass
    raise ValueError(f"无法解析模型输出为 JSON: {text[:200]}")


# ─── 抽取执行 ──────────────────────────────────────────────


def extract_one(chunk: dict[str, Any], prompt: str, api_base: str, api_key: str, model: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": "你是知识图谱抽取引擎，只输出 JSON。"},
        {"role": "user", "content": prompt + "\n\n文本：\n" + chunk["content"]},
    ]
    response = chat_completion(messages, api_base, api_key, model)
    return parse_json_response(response)


def build_prompt(schema: str = "", type_mode: str = "loose") -> str:
    prompt = DEFAULT_PROMPT
    if schema and schema.strip():
        prompt += "\n" + SCHEMA_INSTRUCTION.format(schema=schema.strip())
        prompt += TYPE_MODE_INSTRUCTION.get(type_mode, TYPE_MODE_INSTRUCTION["loose"])
    return prompt


# ─── 类型预设 ───────────────────────────────────────────────


def default_presets_path() -> Path:
    return Path(__file__).resolve().parent.parent / "type_presets.json"


def load_presets(presets_file: str | None = None) -> dict[str, Any]:
    """读取类型预设文件（默认技能内置 type_presets.json）；文件缺失时返回空结构。"""
    path = Path(presets_file) if presets_file else default_presets_path()
    if not path.exists():
        return {"version": 1, "default_preset": "default", "presets": {}, "routes": []}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def expand_preset(data: dict[str, Any], name: str) -> tuple[list[str], list[str]]:
    """沿 extends 继承链展开预设，返回 (entity_types, relation_types)，去重保序（自身在前，基类补充）。"""
    presets = data.get("presets") or {}
    if name not in presets:
        raise KeyError(name)
    entity_types: list[str] = []
    relation_types: list[str] = []
    seen_e: set[str] = set()
    seen_r: set[str] = set()

    def collect(preset_name: str, visited: set[str]) -> None:
        if preset_name in visited:
            return
        visited.add(preset_name)
        node = presets.get(preset_name) or {}
        for t in node.get("entity_types") or []:
            if t not in seen_e:
                seen_e.add(t)
                entity_types.append(t)
        for t in node.get("relation_types") or []:
            if t not in seen_r:
                seen_r.add(t)
                relation_types.append(t)
        for base in node.get("extends") or []:
            if base in presets:
                collect(base, visited)

    collect(name, set())
    return entity_types, relation_types


def preset_to_schema(data: dict[str, Any], name: str) -> str:
    """把预设展开成 schema 文本：'实体类型: A/B/C; 关系类型: X/Y/Z'。"""
    entity_types, relation_types = expand_preset(data, name)
    parts = []
    if entity_types:
        parts.append("实体类型: " + "/".join(entity_types))
    if relation_types:
        parts.append("关系类型: " + "/".join(relation_types))
    return "; ".join(parts)


def list_presets(data: dict[str, Any]) -> str:
    presets = data.get("presets") or {}
    return ", ".join(sorted(presets)) or "(无)"


def merge_graphs(graphs: list[dict[str, Any]]) -> dict[str, Any]:
    """跨块合并：实体按 (normalized_name, label) 去重，关系按 id 去重并合并 sources。"""
    entity_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    relation_by_id: dict[str, dict[str, Any]] = {}
    for graph in graphs:
        for entity in graph.get("entities") or []:
            key = (entity.get("normalized_name") or normalize_entity_name(entity.get("text", "")),
                   entity.get("label") or "Entity")
            existing = entity_by_key.get(key)
            if existing is None:
                entity_by_key[key] = entity
            else:
                known = {(a["text"], a["label"]) for a in existing.get("attributes") or []}
                for attr in entity.get("attributes") or []:
                    if (attr["text"], attr["label"]) not in known:
                        existing.setdefault("attributes", []).append(attr)
                        known.add((attr["text"], attr["label"]))
                # 跨块合并描述：取最长非空原文摘录
                existing_desc = str(existing.get("description") or "").strip()
                incoming_desc = str(entity.get("description") or "").strip()
                if incoming_desc and (not existing_desc or len(incoming_desc) > len(existing_desc)):
                    existing["description"] = incoming_desc
        for relation in graph.get("relations") or []:
            existing = relation_by_id.get(relation["id"])
            if existing is None:
                relation_by_id[relation["id"]] = relation
            else:
                # 同一关系来自多个来源：sources 取并集（增量删除时避免误删共享关系）
                known_sources = set(existing.get("sources") or [])
                for s in relation.get("sources") or []:
                    if s not in known_sources:
                        existing.setdefault("sources", []).append(s)
                        known_sources.add(s)
    return {
        "entities": list(entity_by_key.values()),
        "relations": list(relation_by_id.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="抽取知识图谱实体-关系数据")
    parser.add_argument("--input", help="文本文件 / 目录 / JSONL / JSON 数组；缺省读 stdin")
    parser.add_argument("--stdin", action="store_true", help="从 stdin 读取文本")
    parser.add_argument("--output", help="输出 JSON 文件；缺省写 stdout")
    parser.add_argument("--model", default=os.getenv("GRAPH_EXTRACTION_MODEL") or "gpt-4o-mini",
                        help="模型名（默认取 GRAPH_EXTRACTION_MODEL）")
    parser.add_argument("--api-base", default=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1")
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--schema", default="", help="抽取 Schema 约束，如：实体类型: 法规/机构/产品")
    parser.add_argument("--type-preset", default="", help="使用类型预设（type_presets.json 中的 key，展开为 schema）")
    parser.add_argument("--type-presets-file", default="", help="自定义类型预设文件路径（默认技能内置 type_presets.json）")
    parser.add_argument("--type-mode", default="loose", choices=["strict", "loose"],
                        help="类型约束模式：strict=只允许 Schema 类型；loose=允许少量补充（默认）")
    parser.add_argument("--kb-id", default="default", help="命名空间 ID，用于生成确定性实体/关系 ID")
    parser.add_argument("--max-chars", type=int, default=2000, help="每块最大字符数（默认 2000）")
    parser.add_argument("--concurrency", type=int, default=4, help="并发抽取数（默认 4）")
    parser.add_argument("--max-retries", type=int, default=2, help="单块失败重试次数（默认 2）")
    args = parser.parse_args()

    if not args.api_key:
        print("error: 需要 --api-key 或环境变量 OPENAI_API_KEY", file=sys.stderr)
        return 1

    # 类型预设解析：显式 --schema 优先于 --type-preset 展开
    schema = args.schema
    preset_name = ""
    if not schema.strip() and args.type_preset:
        data = load_presets(args.type_presets_file or None)
        try:
            schema = preset_to_schema(data, args.type_preset)
            preset_name = args.type_preset
        except KeyError:
            print(f"error: 预设 [{args.type_preset}] 不存在。可用预设: {list_presets(data)}",
                  file=sys.stderr)
            return 1

    records = read_input(None if args.stdin else args.input)
    chunks = split_chunks(records, args.max_chars)
    prompt = build_prompt(schema, args.type_mode)
    print(f"info: {len(records)} 输入记录 -> {len(chunks)} 块，模型 {args.model}，并发 {args.concurrency}"
          f"{'，类型预设 ' + preset_name if preset_name else ''}"
          f"{'，类型模式 ' + args.type_mode if schema else ''}",
          file=sys.stderr)

    def worker(chunk: dict[str, Any]) -> dict[str, Any] | None:
        last_error: Exception | None = None
        for attempt in range(args.max_retries + 1):
            try:
                raw = extract_one(chunk, prompt, args.api_base, args.api_key, args.model)
                source = str(chunk.get("chunk_id") or "").strip() or None
                return normalize_extraction_result(raw, args.kb_id, source=source)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        chunk_id = chunk.get("chunk_id", "")
        print(f"warn: 块 {chunk_id} 抽取失败（重试 {args.max_retries} 次）: {last_error}", file=sys.stderr)
        return None

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        results = list(pool.map(worker, chunks))

    graphs = [r for r in results if r is not None]
    merged = merge_graphs(graphs)
    output = {
        **merged,
        "metadata": {
            "schema_version": 1,
            "kb_id": args.kb_id,
            "source_records": len(records),
            "chunks": len(chunks),
            "failed_chunks": len(chunks) - len(graphs),
            "entity_count": len(merged["entities"]),
            "relation_count": len(merged["relations"]),
        },
    }
    payload = json.dumps(output, ensure_ascii=False, indent=2)
    summary = (f"ok: {output['metadata']['entity_count']} entities, "
               f"{output['metadata']['relation_count']} relations"
               f"{', failed ' + str(output['metadata']['failed_chunks']) if output['metadata']['failed_chunks'] else ''}")
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(summary + f" -> {args.output}", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
