#!/usr/bin/env python3
"""知识图谱 Postgres 同步脚本（含强制运行门禁）。

门禁规则（本脚本机械强制 + SKILL.md 流程约束，双重保险）：
1. --mode 必填，无默认值：full=全量重建，incremental=增量更新；
2. 不加 --apply 时只打印执行计划（plan），绝不写库；
3. incremental 模式必须有 --tracked（上次入库状态），否则拒绝执行。

用法：
  # 首次全量初始化
  python3 sync_graph_pg.py --mode full --files-dir docs/ --kb-id kb1 \
      --dsn postgres://user:pass@localhost:5432/db \
      --model gpt-4o-mini --api-key $OPENAI_API_KEY --apply

  # 增量更新（先看计划，确认后再 --apply）
  python3 sync_graph_pg.py --mode incremental --files-dir docs/ \
      --tracked tracked.json --kb-id kb1 --dsn postgres://... [--apply]

  # 只打印计划（不连库）
  python3 sync_graph_pg.py --mode incremental --files-dir docs/ --tracked tracked.json --kb-id kb1

环境变量：OPENAI_API_KEY / OPENAI_BASE_URL。Postgres 驱动：psycopg（优先）或 psycopg2。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_graph import (  # noqa: E402
    build_prompt,
    extract_one,
    load_presets,
    merge_graphs,
    normalize_extraction_result,
    preset_to_schema,
    split_chunks,
)

# ─── Postgres 驱动 ──────────────────────────────────────────

try:
    import psycopg  # type: ignore

    def connect(dsn: str):
        return psycopg.connect(dsn)
except ImportError:
    try:
        import psycopg2  # type: ignore

        def connect(dsn: str):
            return psycopg2.connect(dsn)
    except ImportError:
        connect = None  # type: ignore

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS graph_entities (
  kb_id text NOT NULL,
  entity_id text NOT NULL,
  text text NOT NULL,
  label text NOT NULL DEFAULT 'Entity',
  normalized_name text NOT NULL,
  description text NOT NULL DEFAULT '',
  attributes jsonb NOT NULL DEFAULT '[]',
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kb_id, entity_id)
);
ALTER TABLE graph_entities ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS graph_triples (
  kb_id text NOT NULL,
  triple_id text NOT NULL,
  source_id text NOT NULL,
  target_id text NOT NULL,
  source_text text NOT NULL,
  target_text text NOT NULL,
  text text NOT NULL,
  label text NOT NULL DEFAULT 'RELATED_TO',
  sources jsonb NOT NULL DEFAULT '[]',
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kb_id, triple_id)
);
CREATE INDEX IF NOT EXISTS idx_triples_source ON graph_triples (kb_id, source_id);
CREATE INDEX IF NOT EXISTS idx_triples_target ON graph_triples (kb_id, target_id);
"""

UPSERT_ENTITY_SQL = """
INSERT INTO graph_entities (kb_id, entity_id, text, label, normalized_name, description, attributes)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (kb_id, entity_id) DO UPDATE SET
  text = EXCLUDED.text, label = EXCLUDED.label,
  normalized_name = EXCLUDED.normalized_name,
  description = EXCLUDED.description, attributes = EXCLUDED.attributes,
  updated_at = now()
"""

UPSERT_TRIPLE_SQL = """
INSERT INTO graph_triples (kb_id, triple_id, source_id, target_id,
                           source_text, target_text, text, label, sources)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (kb_id, triple_id) DO UPDATE SET
  source_id = EXCLUDED.source_id, target_id = EXCLUDED.target_id,
  source_text = EXCLUDED.source_text, target_text = EXCLUDED.target_text,
  text = EXCLUDED.text, label = EXCLUDED.label, sources = EXCLUDED.sources,
  updated_at = now()
"""

DELETE_TRIPLES_BY_SOURCE_SQL = """
DELETE FROM graph_triples t
WHERE t.kb_id = %s AND EXISTS (
  SELECT 1 FROM jsonb_array_elements_text(t.sources) s
  WHERE s = %s OR s LIKE %s
)
"""

DELETE_ORPHAN_ENTITIES_SQL = """
DELETE FROM graph_entities e
WHERE e.kb_id = %s AND NOT EXISTS (
  SELECT 1 FROM graph_triples t
  WHERE t.kb_id = %s AND (t.source_id = e.entity_id OR t.target_id = e.entity_id)
)
"""

DELETE_ALL_SQL = """
DELETE FROM graph_triples WHERE kb_id = %s;
DELETE FROM graph_entities WHERE kb_id = %s;
"""


# ─── 文件清单 ───────────────────────────────────────────────


def scan_directory(directory: str) -> dict[str, dict[str, Any]]:
    """扫描目录，file_id = 相对路径（作为 sources 前缀）。"""
    files: dict[str, dict[str, Any]] = {}
    for fp in sorted(Path(directory).rglob("*")):
        if not fp.is_file():
            continue
        relative = str(fp.relative_to(directory))
        if relative.startswith("."):
            continue
        stat = fp.stat()
        files[relative] = {
            "file_id": relative,
            "filename": fp.name,
            "path": str(fp),
            "mtime": stat.st_mtime,
            "size": stat.st_size,
        }
    return files


def load_files_json(path: str) -> dict[str, dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    records: list[dict[str, Any]]
    if path.endswith(".jsonl"):
        records = [json.loads(line) for line in content.splitlines() if line.strip()]
    else:
        records = json.loads(content)
    files: dict[str, dict[str, Any]] = {}
    for record in records:
        fid = str(record.get("id") or record.get("file_id") or "")
        filename = str(record.get("filename") or "").strip()
        if not filename:
            continue
        files[fid] = {
            "file_id": fid,
            "filename": filename,
            "path": str(record.get("path") or filename),
            "mtime": record.get("mtime"),
            "size": record.get("size"),
        }
    return files


# ─── tracked 状态 ───────────────────────────────────────────


def load_tracked(path: str | None) -> dict[str, dict[str, Any]]:
    if not path or not Path(path).exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    tracked: dict[str, dict[str, Any]] = {}
    for fid, value in data.items():
        if isinstance(value, str):  # 兼容旧格式 {file_id: filename}
            tracked[fid] = {"filename": value, "mtime": None, "size": None}
        elif isinstance(value, dict):
            tracked[fid] = value
    return tracked


def save_tracked(path: str, tracked: dict[str, dict[str, Any]]) -> None:
    Path(path).write_text(json.dumps(tracked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def detect_changes(
    current: dict[str, dict[str, Any]], tracked: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    added = sorted(fid for fid in current if fid not in tracked)
    removed = sorted(fid for fid in tracked if fid not in current)
    modified = sorted(
        fid
        for fid in current
        if fid in tracked
        and (
            tracked[fid].get("mtime") is not None
            and current[fid].get("mtime") is not None
            and tracked[fid].get("mtime") != current[fid].get("mtime")
            or tracked[fid].get("size") is not None
            and current[fid].get("size") is not None
            and tracked[fid].get("size") != current[fid].get("size")
        )
    )
    return {"added": added, "removed": removed, "modified": modified}


# ─── 抽取 ───────────────────────────────────────────────────


def extract_file(
    file_info: dict[str, Any],
    prompt: str,
    api_base: str,
    api_key: str,
    model: str,
    kb_id: str,
    max_chars: int,
    retries: int,
) -> dict[str, Any]:
    text = Path(file_info["path"]).read_text(encoding="utf-8", errors="replace")
    chunks = split_chunks([{"content": text, "chunk_id": file_info["file_id"]}], max_chars)
    graphs = []
    for chunk in chunks:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                raw = extract_one(chunk, prompt, api_base, api_key, model)
                graphs.append(normalize_extraction_result(raw, kb_id, source=chunk["chunk_id"]))
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        else:
            raise RuntimeError(f"块 {chunk['chunk_id']} 抽取失败（重试 {retries} 次）: {last_error}")
    return merge_graphs(graphs)


# ─── 类型路由 ──────────────────────────────────────────────


def load_type_map(path: str) -> dict[str, Any]:
    """读取类型路由文件：{"routes": [{"match": "正则", "preset": "..."}], "default": "..."} 或直接数组。"""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return {"routes": data, "default": ""}
    return data


def resolve_preset_for(file_id: str, routes: list[dict[str, Any]], default_preset: str) -> str:
    """按 file_id（相对路径）匹配类型路由；未命中返回 default_preset。"""
    for route in routes:
        try:
            if re.search(str(route.get("match") or ""), file_id):
                return str(route.get("preset") or default_preset)
        except re.error:
            continue
    return default_preset


# ─── 写库 ───────────────────────────────────────────────────


def upsert_graph(cursor, kb_id: str, graph: dict[str, Any]) -> None:
    for entity in graph.get("entities") or []:
        cursor.execute(
            UPSERT_ENTITY_SQL,
            (
                kb_id,
                entity["id"],
                entity["text"],
                entity["label"],
                entity.get("normalized_name", entity["text"]),
                entity.get("description") or "",
                json.dumps(entity.get("attributes") or [], ensure_ascii=False),
            ),
        )
    for relation in graph.get("relations") or []:
        cursor.execute(
            UPSERT_TRIPLE_SQL,
            (
                kb_id,
                relation["id"],
                relation["source_id"],
                relation["target_id"],
                relation["source_text"],
                relation["target_text"],
                relation["text"],
                relation["label"],
                json.dumps(relation.get("sources") or [], ensure_ascii=False),
            ),
        )


def delete_triples_by_source(cursor, kb_id: str, file_id: str) -> int:
    cursor.execute(DELETE_TRIPLES_BY_SOURCE_SQL, (kb_id, file_id, file_id + "#%"))
    return cursor.rowcount


def delete_orphan_entities(cursor, kb_id: str) -> int:
    cursor.execute(DELETE_ORPHAN_ENTITIES_SQL, (kb_id, kb_id))
    return cursor.rowcount


# ─── 主流程 ─────────────────────────────────────────────────


def print_plan(mode: str, kb_id: str, changes: dict[str, list[str]] | None, file_count: int) -> None:
    print(f"[plan] kb_id={kb_id} mode={mode} 当前文件数={file_count}")
    if mode == "full":
        print(f"[plan] 将全量重建 kb_id={kb_id}：重新抽取全部 {file_count} 个文件，"
              "先清空该库的 entities/triples 再写入")
        return
    assert changes is not None
    print(f"[plan] 新增 {len(changes['added'])} 个文件: {', '.join(changes['added'][:20]) or '无'}"
          f"{' …' if len(changes['added']) > 20 else ''}")
    print(f"[plan] 删除 {len(changes['removed'])} 个文件: {', '.join(changes['removed'][:20]) or '无'}"
          "（将删除其来源三元组并回收孤立实体）")
    print(f"[plan] 修改 {len(changes['modified'])} 个文件: {', '.join(changes['modified'][:20]) or '无'}"
          "（将重抽并覆盖）")


def main() -> int:
    parser = argparse.ArgumentParser(description="知识图谱 Postgres 同步（门禁：--mode 必填，--apply 才写库）")
    parser.add_argument("--mode", required=True, choices=["full", "incremental"],
                        help="full=全量重建；incremental=增量更新。运行前必须先与用户确认模式")
    parser.add_argument("--files-dir", help="扫描目录（file_id=相对路径）")
    parser.add_argument("--files-json", help="文件清单 JSON/JSONL：[{id|file_id, filename, path?, mtime?, size?}]")
    parser.add_argument("--tracked", help="上次入库状态 JSON（incremental 必需）")
    parser.add_argument("--tracked-output", help="tracked 写回路径（默认同 --tracked）")
    parser.add_argument("--dsn", help="Postgres 连接串（写库必需）")
    parser.add_argument("--kb-id", required=True, help="知识库/命名空间 ID")
    parser.add_argument("--model", default=os.getenv("GRAPH_EXTRACTION_MODEL") or "gpt-4o-mini")
    parser.add_argument("--api-base", default=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1")
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--schema", default="", help="抽取 Schema 约束（显式指定时优先于类型预设）")
    parser.add_argument("--type-map", help="类型路由 JSON 文件：{\"routes\": [{match, preset}], \"default\": \"...\"}")
    parser.add_argument("--type-preset", default="", help="默认类型预设（未匹配路由时使用，type_presets.json 的 key）")
    parser.add_argument("--type-presets-file", default="", help="自定义类型预设文件路径（默认技能内置 type_presets.json）")
    parser.add_argument("--type-mode", default="loose", choices=["strict", "loose"],
                        help="类型约束模式：strict=只允许预设类型；loose=允许少量补充（默认）")
    parser.add_argument("--max-chars", type=int, default=2000)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--apply", action="store_true", help="执行写库；不加则只打印计划")
    args = parser.parse_args()

    # ── 门禁 1：模式已由 required 强制，这里只做一致性检查 ──
    if args.mode == "incremental" and not args.tracked:
        print("error: 增量模式需要 --tracked（上次入库状态文件）。首次使用请用 --mode full 全量初始化。",
              file=sys.stderr)
        return 1

    # ── 读取文件清单 ──
    if args.files_dir:
        current = scan_directory(args.files_dir)
    elif args.files_json:
        current = load_files_json(args.files_json)
    else:
        print("error: 需要 --files-dir 或 --files-json", file=sys.stderr)
        return 1
    if not current:
        print("error: 文件清单为空", file=sys.stderr)
        return 1

    # ── 类型路由配置（显式 --schema 优先；否则按 type-map/preset 路由）──
    schema = args.schema
    routes: list[dict[str, Any]] = []
    default_preset = args.type_preset or ""
    presets_data: dict[str, Any] | None = None
    if not schema.strip() and (args.type_map or args.type_preset):
        if args.type_map:
            type_map = load_type_map(args.type_map)
            routes = type_map.get("routes") or []
            default_preset = str(type_map.get("default") or default_preset)
        presets_data = load_presets(args.type_presets_file or None)

    def prompt_for(file_id: str) -> str:
        if schema.strip():
            return build_prompt(schema, args.type_mode)
        preset = resolve_preset_for(file_id, routes, default_preset)
        if preset and presets_data:
            try:
                return build_prompt(preset_to_schema(presets_data, preset), args.type_mode)
            except KeyError:
                print(f"warn: 预设 [{preset}] 不存在，回退无 schema", file=sys.stderr)
        return build_prompt("", args.type_mode)

    if args.mode == "full":
        print_plan("full", args.kb_id, None, len(current))
    else:
        tracked = load_tracked(args.tracked)
        if not tracked:
            print("error: --tracked 文件为空或不存在，无法增量。首次请用 --mode full 初始化。", file=sys.stderr)
            return 1
        changes = detect_changes(current, tracked)
        if not changes["added"] and not changes["removed"] and not changes["modified"]:
            print("[plan] 无变更（新增 0 / 删除 0 / 修改 0），图谱已是最新，无需执行。")
            return 0
        print_plan("incremental", args.kb_id, changes, len(current))

    if routes or default_preset:
        counts: dict[str, int] = {}
        for fid in current:
            preset = resolve_preset_for(fid, routes, default_preset) or "(无预设)"
            counts[preset] = counts.get(preset, 0) + 1
        print("[plan] 类型路由: " + ", ".join(f"{k}×{v}" for k, v in counts.items())
              + f"（模式: {args.type_mode}）")

    if not args.apply:
        print("[plan] 未加 --apply，仅打印计划，未做任何写库操作。确认无误后加 --apply 执行。")
        return 0

    # ── 门禁 2：写库前置条件 ──
    if not args.dsn:
        print("error: 写库需要 --dsn（Postgres 连接串）", file=sys.stderr)
        return 1
    if connect is None:
        print("error: 缺少 Postgres 驱动，请先 pip install psycopg[binary]（或 psycopg2）", file=sys.stderr)
        return 1
    if not args.api_key:
        print("error: 需要 --api-key 或环境变量 OPENAI_API_KEY", file=sys.stderr)
        return 1

    def extract_worker(fid: str) -> tuple[str, dict[str, Any]]:
        return fid, extract_file(
            current[fid], prompt_for(fid), args.api_base, args.api_key, args.model,
            args.kb_id, args.max_chars, args.max_retries,
        )

    conn = connect(args.dsn)
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(SCHEMA_SQL)

                if args.mode == "full":
                    cursor.execute(DELETE_ALL_SQL, (args.kb_id, args.kb_id))
                    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
                        results = list(pool.map(extract_worker, sorted(current)))
                    entity_total = relation_total = 0
                    for fid, graph in results:
                        upsert_graph(cursor, args.kb_id, graph)
                        entity_total += len(graph.get("entities") or [])
                        relation_total += len(graph.get("relations") or [])
                    new_tracked = {fid: {k: v for k, v in info.items() if k != "path"}
                                   for fid, info in current.items()}
                    print(f"ok: 全量重建完成 kb_id={args.kb_id}，写入实体 {entity_total}，关系 {relation_total}",
                          file=sys.stderr)
                else:
                    tracked = load_tracked(args.tracked)
                    changes = detect_changes(current, tracked)
                    # 删除
                    removed_count = 0
                    for fid in changes["removed"]:
                        removed_count += delete_triples_by_source(cursor, args.kb_id, fid)
                        tracked.pop(fid, None)
                    # 修改：删旧 → 重抽 → 写新
                    to_extract = changes["added"] + changes["modified"]
                    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
                        results = list(pool.map(extract_worker, to_extract))
                    entity_total = relation_total = 0
                    for fid, graph in results:
                        if fid in changes["modified"]:
                            delete_triples_by_source(cursor, args.kb_id, fid)
                        upsert_graph(cursor, args.kb_id, graph)
                        entity_total += len(graph.get("entities") or [])
                        relation_total += len(graph.get("relations") or [])
                        tracked[fid] = {k: v for k, v in current[fid].items() if k != "path"}
                    orphan_count = delete_orphan_entities(cursor, args.kb_id)
                    save_tracked(args.tracked_output or args.tracked, tracked)
                    print(
                        f"ok: 增量完成 新增{len(changes['added'])} 修改{len(changes['modified'])} "
                        f"删除{len(changes['removed'])}；删除旧三元组 {removed_count} 条，"
                        f"写入实体 {entity_total}，关系 {relation_total}，回收孤立实体 {orphan_count} 个",
                        file=sys.stderr,
                    )
    except Exception as exc:  # noqa: BLE001
        print(f"error: 写库失败，事务已回滚: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
