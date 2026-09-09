#!/usr/bin/env python3
"""知识图谱 Postgres 同步脚本（Agent 直抽版 · 纯写库，无 LLM、无 api-key）。

输入：Agent 抽取并规范化后的图谱 JSON（normalize_graph.py --merge 的输出，
含 entities[].description 与 relations[].sources）。

门禁规则（本脚本机械强制 + SKILL.md 流程约束，双重保险）：
1. --mode 必填，无默认值：full=全量重建，incremental=增量更新；
2. 不加 --apply 时只打印执行计划（plan），绝不写库；
3. 首次建库必须走 full（incremental 的删除基线在库内，库为空时等价纯 UPSERT）。

用法：
  # 首次全量初始化（先问用户确认"全量"，再执行）
  python3 sync_graph_pg.py --mode full --graph graph.json --kb-id kb1 \
      --dsn postgres://user:pass@localhost:5432/db --apply

  # 增量更新（先问用户确认"新增"，先看计划再 --apply）
  python3 sync_graph_pg.py --mode incremental --graph graph.json --kb-id kb1 \
      --dsn postgres://... [--apply]

增量语义（图级 diff，删除基线在库内，无需 tracked 文件）：
- 新增/修改：graph.json 中的全部关系 UPSERT（幂等 ON CONFLICT）；
- 删除：读取库内现有 sources 前缀，与 graph.json 中的 sources 前缀对比，
  不再出现的来源前缀 → 按前缀删除三元组 → 回收孤立实体（纯 SQL 无 AI）。
  整体单事务，失败回滚。

Postgres 驱动：psycopg（优先）或 psycopg2。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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

SELECT_SOURCES_SQL = """
SELECT DISTINCT s FROM graph_triples t, jsonb_array_elements_text(t.sources) AS s
WHERE t.kb_id = %s
"""


# ── 图谱输入 ────────────────────────────────────────────────


def load_graph(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        graph = json.load(fh)
    if not isinstance(graph, dict):
        raise ValueError("图谱 JSON 必须是对象")
    entities = graph.get("entities") or []
    relations = graph.get("relations") or []
    if not isinstance(entities, list) or not isinstance(relations, list):
        raise ValueError("图谱 JSON 需要 entities / relations 数组")
    for relation in relations:
        if not relation.get("id"):
            raise ValueError("关系缺少 id（请先用 normalize_graph.py 规范化）")
    return graph


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


def delete_triples_by_source(cursor, kb_id: str, source_prefix: str) -> int:
    cursor.execute(DELETE_TRIPLES_BY_SOURCE_SQL, (kb_id, source_prefix, source_prefix + "#%"))
    return cursor.rowcount


def delete_orphan_entities(cursor, kb_id: str) -> int:
    cursor.execute(DELETE_ORPHAN_ENTITIES_SQL, (kb_id, kb_id))
    return cursor.rowcount


def source_prefixes(graph: dict[str, Any]) -> set[str]:
    prefixes: set[str] = set()
    for relation in graph.get("relations") or []:
        for source in relation.get("sources") or []:
            prefixes.add(str(source).split("#", 1)[0])
    return prefixes


# ── 主流程 ─────────────────────────────────────────────────


def print_plan(mode: str, kb_id: str, graph: dict[str, Any]) -> None:
    entities = len(graph.get("entities") or [])
    relations = len(graph.get("relations") or [])
    if mode == "full":
        print(f"[plan] kb_id={kb_id} mode=full 将全量重建：先清空该库的 entities/triples，"
              f"再写入 graph.json 的 {entities} 个实体 / {relations} 条关系")
        return
    new_prefixes = sorted(source_prefixes(graph))
    print(f"[plan] kb_id={kb_id} mode=incremental 图级 diff：UPSERT graph.json 的 "
          f"{entities} 个实体 / {relations} 条关系（{len(new_prefixes)} 个来源前缀）")
    print(f"[plan] 将删除库中存在但 graph.json 中不再出现的来源前缀（执行时与库内对比，纯 SQL 无 AI），"
          "随后回收孤立实体")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="知识图谱 Postgres 同步（Agent 直抽版：输入规范化图 JSON；门禁 --mode 必填、--apply 才写库）")
    parser.add_argument("--mode", required=True, choices=["full", "incremental"],
                        help="full=全量重建；incremental=增量更新。运行前必须先与用户确认模式")
    parser.add_argument("--graph", required=True,
                        help="Agent 抽取并规范化后的图谱 JSON（normalize_graph.py --merge 的输出）")
    parser.add_argument("--kb-id", required=True, help="知识库/命名空间 ID")
    parser.add_argument("--dsn", help="Postgres 连接串（写库必需）")
    parser.add_argument("--apply", action="store_true", help="执行写库；不加则只打印计划")
    args = parser.parse_args()

    try:
        graph = load_graph(args.graph)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: 读取图谱失败: {exc}", file=sys.stderr)
        return 1
    if not graph.get("entities") and not graph.get("relations"):
        print("error: 图谱为空（entities / relations 均无内容）", file=sys.stderr)
        return 1

    print_plan(args.mode, args.kb_id, graph)

    if not args.apply:
        print("[plan] 未加 --apply，仅打印计划，未做任何写库操作。确认无误后加 --apply 执行。")
        return 0

    if not args.dsn:
        print("error: 写库需要 --dsn（Postgres 连接串）", file=sys.stderr)
        return 1
    if connect is None:
        print("error: 缺少 Postgres 驱动，请先 pip install psycopg[binary]（或 psycopg2）", file=sys.stderr)
        return 1

    conn = connect(args.dsn)
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(SCHEMA_SQL)
                if args.mode == "full":
                    cursor.execute(DELETE_ALL_SQL, (args.kb_id, args.kb_id))
                    upsert_graph(cursor, args.kb_id, graph)
                    print(f"ok: 全量重建完成 kb_id={args.kb_id}，写入实体 {len(graph.get('entities') or [])}，"
                          f"关系 {len(graph.get('relations') or [])}", file=sys.stderr)
                else:
                    cursor.execute(SELECT_SOURCES_SQL, (args.kb_id,))
                    old_prefixes = {str(row[0]).split("#", 1)[0] for row in cursor.fetchall()}
                    new_prefixes = source_prefixes(graph)
                    removed = sorted(old_prefixes - new_prefixes)
                    deleted_count = 0
                    for prefix in removed:
                        deleted_count += delete_triples_by_source(cursor, args.kb_id, prefix)
                    upsert_graph(cursor, args.kb_id, graph)
                    orphan_count = delete_orphan_entities(cursor, args.kb_id)
                    print(
                        f"ok: 增量完成 删除来源前缀 {len(removed)} 个"
                        f"{'（' + ', '.join(removed[:10]) + '…' if len(removed) > 10 else ''}）"
                        f"，删除旧三元组 {deleted_count} 条，"
                        f"写入实体 {len(graph.get('entities') or [])}，关系 {len(graph.get('relations') or [])}，"
                        f"回收孤立实体 {orphan_count} 个",
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
