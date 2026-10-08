#!/usr/bin/env python3
"""为规范化图谱补齐教材定位与知识点编码，不修改实体 ID 或关系。"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from source_meta import extract_source_meta


CODE_FORMAT = "{book_code}-C{chapter_no:02d}-S{section_no:02d}-KP{knowledge_no:03d}"
EXTENSION_KEYS = (
    "code", "book_code", "chapter_no", "section_no", "knowledge_no", "location_codes",
)


def positive_integer(value: Any, context: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{context} 必须是正整数")
    return value


def validate_graph(graph: Any, context: str) -> None:
    if not isinstance(graph, dict):
        raise ValueError(f"{context} 必须是图谱对象")
    if not isinstance(graph.get("entities"), list) or not isinstance(graph.get("relations"), list):
        raise ValueError(f"{context} 必须包含 entities / relations 数组")
    if not isinstance(graph.get("metadata", {}), dict):
        raise ValueError(f"{context}.metadata 必须是对象")
    entity_ids: set[str] = set()
    for entity in graph["entities"]:
        if not isinstance(entity, dict) or not isinstance(entity.get("id"), str) or not entity["id"]:
            raise ValueError(f"{context} 的实体必须具有非空字符串 id")
        if entity["id"] in entity_ids:
            raise ValueError(f"{context} 实体 id 重复：{entity['id']}")
        entity_ids.add(entity["id"])
    for relation in graph["relations"]:
        if not isinstance(relation, dict):
            raise ValueError(f"{context} 的关系必须是对象")
        for endpoint in ("source_id", "target_id"):
            if not isinstance(relation.get(endpoint), str) or relation[endpoint] not in entity_ids:
                raise ValueError(f"{context} 存在无效关系端点：{relation.get(endpoint)}")


def index_locations(locations: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(locations, list) or not locations:
        raise ValueError("location_map.json 必须是非空数组")
    by_lesson: dict[str, dict[str, Any]] = {}
    by_position: dict[tuple[int, int], str] = {}
    unit_chapters: dict[str, int] = {}
    chapter_units: dict[int, str] = {}
    textbook_ids: set[str] = set()
    for raw in locations:
        if not isinstance(raw, dict):
            raise ValueError("每条章节定位必须是对象")
        location: dict[str, Any] = {}
        for key in ("textbook_id", "unit_id", "lesson_id"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"定位字段 {key} 必须是非空字符串")
            location[key] = value.strip()
        for key in ("chapter_no", "section_no"):
            location[key] = positive_integer(raw.get(key), key)
        textbook_ids.add(location["textbook_id"])
        lesson_id = location["lesson_id"]
        unit_id = location["unit_id"]
        chapter_no = location["chapter_no"]
        position = (chapter_no, location["section_no"])
        if lesson_id in by_lesson and by_lesson[lesson_id] != location:
            raise ValueError(f"课时 {lesson_id} 的章节定位冲突")
        if position in by_position and by_position[position] != lesson_id:
            raise ValueError(f"章节位置 {position} 对应多个课时")
        if unit_chapters.get(unit_id, chapter_no) != chapter_no:
            raise ValueError(f"单元 {unit_id} 对应多个章节号")
        if chapter_units.get(chapter_no, unit_id) != unit_id:
            raise ValueError(f"章节号 {chapter_no} 对应多个单元")
        by_lesson[lesson_id] = location
        by_position[position] = lesson_id
        unit_chapters[unit_id] = chapter_no
        chapter_units[chapter_no] = unit_id
    if len(textbook_ids) != 1:
        raise ValueError("一个 book_code 只能对应一本教材，请按教材拆分图谱与定位表")
    return by_lesson


def entity_locations(
    entity: dict[str, Any], by_lesson: dict[str, dict[str, Any]],
    sources: list[str], source_root: str | None,
) -> list[dict[str, Any]]:
    attributes = entity.get("attributes") or []
    if not isinstance(attributes, list) or any(not isinstance(item, dict) for item in attributes):
        raise ValueError(f"实体 {entity['id']} 的 attributes 必须是对象数组")
    values: dict[str, set[str]] = defaultdict(set)
    for attribute in attributes:
        label = attribute.get("label")
        if label in ("textbookId", "unitId", "lessonId"):
            text = str(attribute.get("text") or "").strip()
            if text:
                values[label].add(text)
    if not values["lessonId"]:
        if entity.get("location_codes"):
            for location in entity["location_codes"]:
                if not isinstance(location, dict) or location != by_lesson.get(str(location.get("lesson_id"))):
                    raise ValueError(f"实体 {entity['id']} 的已有 location_codes 与定位表不一致")
                values["lessonId"].add(location["lesson_id"])
        else:
            for source in sources:
                meta = extract_source_meta(source, source_root)
                if not meta.get("lessonId"):
                    raise ValueError(f"实体 {entity['id']} 的来源无法解析课时：{source}")
                location = by_lesson.get(meta["lessonId"])
                if location is not None:
                    for attribute_key, location_key in (("textbookId", "textbook_id"), ("unitId", "unit_id")):
                        if meta.get(attribute_key) and meta[attribute_key] != location[location_key]:
                            raise ValueError(f"来源 {source} 的 {attribute_key} 与定位表不一致")
                values["lessonId"].add(meta["lessonId"])
    if not values["lessonId"]:
        raise ValueError(f"实体 {entity['id']} 缺少可验证的课时归属；请补齐 lessonId 或来源，不能猜测章节")
    missing = values["lessonId"] - by_lesson.keys()
    if missing:
        raise ValueError(f"实体 {entity['id']} 的课时未在定位表中：{sorted(missing)}")
    locations = [by_lesson[lesson_id] for lesson_id in values["lessonId"]]
    for attribute_key, location_key in (("textbookId", "textbook_id"), ("unitId", "unit_id")):
        if values[attribute_key] and values[attribute_key] != {item[location_key] for item in locations}:
            raise ValueError(f"实体 {entity['id']} 的 {attribute_key} 与课时定位不一致")
    return sorted(locations, key=lambda item: (item["chapter_no"], item["section_no"], item["lesson_id"]))


def code_scheme(book_code: str) -> dict[str, Any]:
    return {
        "version": 1,
        "book_code": book_code,
        "format": CODE_FORMAT,
        "primary_key": "id",
        "code_role": "human_readable_location_code",
        "multi_location_policy": "earliest_location_is_primary",
    }


def existing_assignments(
    graph: dict[str, Any], book_code: str,
) -> tuple[dict[str, tuple[int, int, int]], dict[tuple[int, int], int]]:
    existing_scheme = graph.get("metadata", {}).get("entity_code_scheme")
    if existing_scheme is not None:
        if not isinstance(existing_scheme, dict) or any(
            existing_scheme.get(key) != value for key, value in code_scheme(book_code).items()
        ):
            raise ValueError("已有 entity_code_scheme 与当前编码规则不一致")
    assignments: dict[str, tuple[int, int, int]] = {}
    maximums: dict[tuple[int, int], int] = defaultdict(int)
    used: set[tuple[int, int, int]] = set()
    for entity in graph["entities"]:
        if not any(key in entity for key in EXTENSION_KEYS):
            continue
        if any(key not in entity for key in EXTENSION_KEYS):
            raise ValueError(f"实体 {entity['id']} 的已有扩展字段不完整")
        chapter_no = positive_integer(entity["chapter_no"], "chapter_no")
        section_no = positive_integer(entity["section_no"], "section_no")
        knowledge_no = positive_integer(entity["knowledge_no"], "knowledge_no")
        assignment = (chapter_no, section_no, knowledge_no)
        expected = CODE_FORMAT.format(
            book_code=book_code, chapter_no=chapter_no, section_no=section_no, knowledge_no=knowledge_no,
        )
        if entity["book_code"] != book_code or entity["code"] != expected:
            raise ValueError(f"实体 {entity['id']} 的已有编码与编号字段不一致")
        if assignment in used:
            raise ValueError(f"已有编码重复：{expected}")
        used.add(assignment)
        assignments[entity["id"]] = assignment
        maximums[(chapter_no, section_no)] = max(maximums[(chapter_no, section_no)], knowledge_no)
    return assignments, maximums


def enrich_graph(
    graph: dict[str, Any], locations: list[dict[str, Any]], book_code: str,
    existing: dict[str, Any] | None = None, source_root: str | None = None,
) -> dict[str, Any]:
    if not isinstance(book_code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", book_code):
        raise ValueError("book_code 必须以大写字母开头，且仅含大写字母、数字或下划线")
    validate_graph(graph, "graph")
    by_lesson = index_locations(locations)
    baseline = graph if existing is None else existing
    validate_graph(baseline, "existing")
    current_kb = graph.get("metadata", {}).get("kb_id")
    previous_kb = baseline.get("metadata", {}).get("kb_id")
    if current_kb and previous_kb and current_kb != previous_kb:
        raise ValueError("当前图谱与已有编码基线的 kb_id 不一致")
    assignments, maximums = existing_assignments(baseline, book_code)
    sources_by_entity: dict[str, list[str]] = defaultdict(list)
    for relation in graph["relations"]:
        sources = relation.get("sources") or []
        if not isinstance(sources, list) or any(not isinstance(source, str) for source in sources):
            raise ValueError("关系的 sources 必须是字符串数组")
        for endpoint in ("source_id", "target_id"):
            sources_by_entity[relation[endpoint]].extend(sources)
    result = copy.deepcopy(graph)
    for entity in result["entities"]:
        resolved = entity_locations(entity, by_lesson, sources_by_entity[entity["id"]], source_root)
        primary = resolved[0]
        position = (primary["chapter_no"], primary["section_no"])
        previous = assignments.get(entity["id"])
        if previous is not None:
            if previous[:2] != position:
                raise ValueError(f"实体 {entity['id']} 的主位置已改变；请确认全量重编码后使用未编码图谱重建")
            knowledge_no = previous[2]
        else:
            maximums[position] += 1
            knowledge_no = maximums[position]
        entity.update({
            "code": CODE_FORMAT.format(
                book_code=book_code, chapter_no=position[0], section_no=position[1], knowledge_no=knowledge_no,
            ),
            "book_code": book_code,
            "chapter_no": position[0],
            "section_no": position[1],
            "knowledge_no": knowledge_no,
            "location_codes": copy.deepcopy(resolved),
        })
    metadata = result.setdefault("metadata", {})
    metadata["entity_code_scheme"] = code_scheme(book_code)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="补齐教材图谱的章节定位与知识点编码")
    parser.add_argument("--graph", required=True, help="normalize_graph.py 生成的基础图谱")
    parser.add_argument("--locations", required=True, help="已核对教材目录顺序的 location_map.json")
    parser.add_argument("--book-code", required=True, help="教材业务编码，如 DXSXLJK")
    parser.add_argument("--existing", help="增量编码基线 graph.json，保留已有实体的编码")
    parser.add_argument("--source-root", help="缺少 lessonId 时，解析关系 sources 中相对文件路径的基准目录")
    parser.add_argument("--output", required=True, help="最终 graph.json，可与 --graph 相同")
    parser.add_argument("--dry-run", action="store_true", help="只验证与打印统计，不写文件")
    args = parser.parse_args()
    try:
        graph = json.loads(Path(args.graph).read_text(encoding="utf-8"))
        locations = json.loads(Path(args.locations).read_text(encoding="utf-8"))
        existing = json.loads(Path(args.existing).read_text(encoding="utf-8")) if args.existing else None
        result = enrich_graph(graph, locations, args.book_code, existing, args.source_root)
        if not args.dry_run:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        status = "dry-run" if args.dry_run else "ok"
        print(f"{status}: {len(result['entities'])} entities, {len(result['relations'])} relations; "
              f"book_code={args.book_code}" + ("（未写盘）" if args.dry_run else f" -> {args.output}"))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
