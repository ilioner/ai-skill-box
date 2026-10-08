# 教材图谱扩展字段与编码

教材/课程图谱默认在规范化合并后运行 `scripts/enrich_graph_codes.py`，将基础图谱补齐为包含业务编码的最终 `graph.json`。通用非教材图谱仍可只输出基础字段。编码是确定性后处理，不由 Agent 自由编造，不替换实体 `id`，也不修改关系。

## 输入准备

使用项目已约定的 `book_code`；若没有约定，向用户确认教材业务编码。不要把示例 `DXSXLJK` 套用到其他教材。

Agent 根据教材目录、来源 frontmatter 与文件对应关系整理 `location_map.json`。顶层为数组，一条记录对应一个真实课时：

```json
[
  {
    "textbook_id": "8407877133787013120",
    "unit_id": "7989331510455501413",
    "lesson_id": "7989338958046233670",
    "chapter_no": 1,
    "section_no": 1
  }
]
```

- 三个 ID 必须是字符串，不能转换成浮点数；`chapter_no`、`section_no` 必须是从 1 开始的正整数。
- `chapter_no` 对应教材的章/单元/项目序号；`section_no` 对应该章内的节/课时/任务序号，每章重新计数。按实际目录核对，不能按雪花 ID 大小或文件名排序猜测。
- `learning_path.json` 仅在其顺序已与教材目录核对后，才能用于生成上述编号；其自动扫描顺序不保证就是教学顺序。
- 一个定位表、一个 `book_code` 只对应一本教材；多个教材应拆分。一个章节位置对应一个课时，一个单元对应一个章号。
- 优先用实体 `attributes` 中的 `lessonId` 查表，保留跨课时的所有位置，不对合并后的 `unitId` 与 `lessonId` 做笛卡尔积。已有 `textbookId` / `unitId` 必须与定位表一致。
- 旧图谱缺少 `lessonId` 时，先校验并使用已有 `location_codes`；若也没有，则从该实体参与关系的 `sources` 解析课时（文件 frontmatter 或约定的三段 ID 文件名，支持 `#partN`）。相对源文件可通过 `--source-root` 定位。只使用来源证据，不借用相邻实体的章节，也不改写原 `attributes`。
- 缺失或冲突的定位会报错且不写输出；应补齐来源信息，不以 `0`、任意章节或虚构编号兜底。没有关系的孤立实体必须通过自身属性或已有定位确定归属。

## 完整字段契约

实体保留基础字段 `id/text/label/normalized_name/description/attributes`，并补齐：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `code` | string | `{book_code}-C{chapter_no:02d}-S{section_no:02d}-KP{knowledge_no:03d}` |
| `book_code` | string | 教材业务编码，大写字母开头，仅含大写字母、数字、下划线 |
| `chapter_no` | integer | 主位置所在的章号 |
| `section_no` | integer | 主位置所在章内的节号 |
| `knowledge_no` | integer | 主位置章/节内唯一的知识点序号 |
| `location_codes` | array | 全部真实位置，每项就是上面的五字段定位对象 |

`location_codes` 去重后按章号、节号排序，最早位置作为主位置。初次编码在每个主位置内按输入 `entities` 数组顺序从 1 编号；数字宽度为最小补零宽度，不截断超过两位/三位的序号。相同输入与定位表可重复得到相同结果。

`metadata` 保留原有 `schema_version/kb_id/merged_sources/entity_count/relation_count` 等已有字段，并增加：

```json
{
  "entity_code_scheme": {
    "version": 1,
    "book_code": "DXSXLJK",
    "format": "{book_code}-C{chapter_no:02d}-S{section_no:02d}-KP{knowledge_no:03d}",
    "primary_key": "id",
    "code_role": "human_readable_location_code",
    "multi_location_policy": "earliest_location_is_primary"
  }
}
```

关系字段 `id/source_id/target_id/source_text/target_text/text/label/sources` 原样保留。稳定引用仍使用 `id`，不要把可读的 `code` 当数据库主键。

## 运行顺序

沿用技能的类型确认与新增/全量确认门禁。不要把最终编码图谱再次送入 raw 规范化器：它只处理基础抽取 Schema，会丢弃这些顶层扩展字段。

```bash
python3 scripts/normalize_graph.py --merge --input raws.jsonl \
    --kb-id my_course --source-root . --output graph.base.json
python3 scripts/enrich_graph_codes.py --graph graph.base.json \
    --locations location_map.json --book-code MYCOURSE --output graph.json
```

增量生成时，保留旧的 `graph.json`，先将当前完整图谱规范化到 `graph.base.json`，再执行：

```bash
python3 scripts/enrich_graph_codes.py --graph graph.base.json \
    --locations location_map.json --book-code MYCOURSE \
    --existing graph.json --output graph.json
```

`--existing` 只提供编码基线，不负责把新旧图谱合并；`--graph` 必须是本次的完整图谱。同一 `id` 且主位置不变时保留编码；新增实体在该位置已有最大序号后追加，不复用当前基线中已占用的号码。此机制不是永久的已删除编码登记簿，外部引用必须使用 `id`。不指定基线时，输入中已有的完整编码也会被保留。

若已有实体的主位置、教材编码或编码规则发生变化，脚本报错，避免静默改码；确认全量重编码后，从未编码的 `graph.base.json` 重新生成，不传 `--existing`。`--dry-run` 执行全部校验但不写文件。

## 渲染与落库边界

2D/3D 渲染器已读取 `code` 并在节点详情展示；下钻仍依赖 `attributes` 中的 `unitId/lessonId`。

本步骤补齐的是 JSON 契约。现有 `sync_graph_pg.py` 不会自动新增数据库列，也不会持久化这些实体顶层扩展字段或 `metadata.entity_code_scheme`；如需数据库保存，应另行扩展表结构与 UPSERT，不能仅凭 JSON 中存在字段就声称已落库。
