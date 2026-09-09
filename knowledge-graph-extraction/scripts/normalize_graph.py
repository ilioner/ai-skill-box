#!/usr/bin/env python3
"""规范化知识图谱抽取结果。

输入 LLM（或其他抽取器）产出的原始 JSON，输出标准化图谱数据：
- 实体按 (规范化名 + label) 去重合并，attributes 取并集
- 关系端点校验：source/target 必须是实体对象，或引用实体列表中的 text/id
- 生成确定性 ID（entity_id / triple_id），重跑幂等

用法：
  python3 normalize_graph.py --input raw.json --output graph.json [--kb-id kb1]
  cat raw.json | python3 normalize_graph.py --stdin
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any


def normalize_entity_name(text: str) -> str:
    """统一实体名称：去首尾空白、小写化、压缩内部连续空白。"""
    return " ".join(str(text).strip().lower().split())


def compute_entity_id(kb_id: str, normalized_name: str, label: str, length: int = 32) -> str:
    return hashlib.sha256(f"{kb_id}:{normalized_name}:{label}".encode("utf-8")).hexdigest()[:length]


def compute_triple_id(
    kb_id: str,
    source_name: str,
    source_label: str,
    relation_type: str,
    target_name: str,
    target_label: str,
    length: int = 32,
) -> str:
    raw = f"{kb_id}:{source_name}:{source_label}:{relation_type}:{target_name}:{target_label}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _normalize_entity(entity: Any, path: str, kb_id: str, id_length: int) -> dict[str, Any]:
    if not isinstance(entity, dict):
        raise ValueError(f"{path} 必须是对象，got {type(entity).__name__}")
    text = str(entity.get("text") or "").strip()
    if not text:
        raise ValueError(f"{path}.text 不能为空")

    attributes = entity.get("attributes") or []
    if not isinstance(attributes, list):
        raise ValueError(f"{path}.attributes 必须是数组")
    normalized_attributes = []
    for attribute in attributes:
        if not isinstance(attribute, dict):
            continue
        attr_text = str(attribute.get("text") or "").strip()
        if not attr_text:
            continue
        normalized_attributes.append(
            {
                "text": attr_text,
                "label": str(attribute.get("label") or "Attribute").strip() or "Attribute",
            }
        )

    label = str(entity.get("label") or "Entity").strip() or "Entity"
    normalized_name = normalize_entity_name(text)
    return {
        "id": compute_entity_id(kb_id, normalized_name, label, id_length),
        "text": text,
        "label": label,
        "normalized_name": normalized_name,
        "description": str(entity.get("description") or "").strip(),
        "attributes": normalized_attributes,
    }


def normalize_extraction_result(
    result: dict[str, Any], kb_id: str, id_length: int = 32, source: str | None = None
) -> dict[str, Any]:
    """将抽取器产出标准化为可入库的图谱数据（实体/关系 + 确定性 ID）。

    source: 来源标识（文件路径 / chunk_id），会写入每条关系的 sources 数组，
            用于增量更新时按来源定位删除。None 表示不追踪来源。
    """
    if not isinstance(result, dict):
        raise ValueError("extraction_result 必须是对象")

    entities = result.get("entities") or []
    relations = result.get("relations") or []
    if not isinstance(entities, list) or not isinstance(relations, list):
        raise ValueError("extraction_result.entities 和 relations 必须是数组")

    # 实体去重合并：key = (normalized_name, label)
    entity_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    entity_refs: dict[str, dict[str, Any]] = {}

    def add_entity(entity: Any, path: str) -> dict[str, Any]:
        normalized = _normalize_entity(entity, path, kb_id, id_length)
        key = (normalized["normalized_name"], normalized["label"])
        existing = entity_by_key.get(key)
        if existing is None:
            entity_by_key[key] = normalized
            existing = normalized
        else:
            _merge_attributes(existing, normalized)
            _merge_description(existing, normalized)
        for ref in _entity_refs(entity, existing):
            entity_refs[ref] = existing
        return existing

    for index, entity in enumerate(entities):
        add_entity(entity, f"entities[{index}]")

    normalized_relations: list[dict[str, Any]] = []
    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            raise ValueError(f"relations[{index}] 必须是对象")
        src = _normalize_relation_endpoint(
            relation.get("source"), entity_refs, add_entity, f"relations[{index}].source"
        )
        tgt = _normalize_relation_endpoint(
            relation.get("target"), entity_refs, add_entity, f"relations[{index}].target"
        )
        text = str(relation.get("text") or "").strip()
        if not text:
            raise ValueError(f"relations[{index}].text 不能为空")
        label = str(relation.get("label") or "RELATED_TO").strip() or "RELATED_TO"
        normalized_relations.append(
            {
                "id": compute_triple_id(
                    kb_id,
                    src["normalized_name"],
                    src["label"],
                    label,
                    tgt["normalized_name"],
                    tgt["label"],
                    id_length,
                ),
                "source_id": src["id"],
                "target_id": tgt["id"],
                "source_text": src["text"],
                "target_text": tgt["text"],
                "text": text,
                "label": label,
                "sources": [source] if source else [],
            }
        )

    return {
        "entities": list(entity_by_key.values()),
        "relations": normalized_relations,
        "metadata": {
            "schema_version": 1,
            "entity_count": len(entity_by_key),
            "relation_count": len(normalized_relations),
            "kb_id": kb_id,
        },
    }


def _normalize_relation_endpoint(
    endpoint: Any, entity_refs: dict[str, dict[str, Any]], add_entity, path: str
) -> dict[str, Any]:
    if isinstance(endpoint, dict):
        return add_entity(endpoint, path)
    endpoint_ref = str(endpoint or "").strip()
    entity = entity_refs.get(endpoint_ref)
    if entity is None:
        raise ValueError(
            f"{path} 必须是实体对象，或引用 entities[].text/id，未找到: {endpoint_ref}"
        )
    return entity


def _entity_refs(raw_entity: Any, entity: dict[str, Any]) -> list[str]:
    refs = [entity["text"]]
    if isinstance(raw_entity, dict):
        entity_id = str(raw_entity.get("id") or "").strip()
        if entity_id:
            refs.append(entity_id)
    return refs


def _merge_attributes(target: dict[str, Any], source: dict[str, Any]) -> None:
    known = {(a["text"], a["label"]) for a in target.get("attributes") or []}
    for attribute in source.get("attributes") or []:
        key = (attribute["text"], attribute["label"])
        if key not in known:
            target.setdefault("attributes", []).append(attribute)
            known.add(key)


def _merge_description(target: dict[str, Any], source: dict[str, Any]) -> None:
    """跨块合并实体描述：取"最长非空"原文摘录（不同 chunk 摘录可能不同，保留信息量最大的）。"""
    existing = str(target.get("description") or "").strip()
    incoming = str(source.get("description") or "").strip()
    if incoming and (not existing or len(incoming) > len(existing)):
        target["description"] = incoming


def load_input(path: str | None) -> dict[str, Any]:
    if path is None:
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    parser = argparse.ArgumentParser(description="规范化知识图谱抽取结果")
    parser.add_argument("--input", help="原始抽取结果 JSON 文件；缺省从 stdin 读取")
    parser.add_argument("--output", help="输出 JSON 文件；缺省写到 stdout")
    parser.add_argument("--kb-id", default="default", help="知识库/命名空间 ID，用于生成确定性实体/关系 ID")
    parser.add_argument("--id-length", type=int, default=32, help="ID 截断长度（默认 32）")
    parser.add_argument("--source", default="", help="来源标识（写入每条关系的 sources 数组，增量更新按来源删除用）")
    args = parser.parse_args()

    try:
        raw = load_input(args.input)
        normalized = normalize_extraction_result(raw, args.kb_id, args.id_length, args.source or None)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(normalized, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        print(f"ok: {normalized['metadata']['entity_count']} entities, "
              f"{normalized['metadata']['relation_count']} relations -> {args.output}", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
