# 抽取 Prompt、Schema 与 Postgres 落库参考

## 默认抽取 Prompt（内置于 extract_graph.py）

要求模型返回严格 JSON，只含 `relations` 数组（source/target 内嵌实体对象）：

```json
{
  "relations": [
    {
      "source": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "target": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "text": "关系显示文本",
      "label": "关系类型"
    }
  ]
}
```

要点：
- `entities` 顶层数组可省略——规范化会自动从 relations 的 source/target 补全实体；
- 关系 `text` 必填，`label` 缺省为 `RELATED_TO`；
- 关系端点可引用实体数组中的 `text` 或 `id`（字符串），也可直接内嵌对象。

## Schema 约束

`--schema` 传入抽取约束，附加在默认 prompt 之后：

```
实体类型: 法规/机构/产品/人物
关系类型: 制定/执行/违反/引用
```

## 类型预设（type_presets.json）

技能根目录的 `type_presets.json` **只内置领域骨架**（`default` 兜底 + 学段基类 + 学科基类），课程级预设由 `suggest_types.py` 模型提议固化，或用户手填。手填格式示例（写入 `presets` 字段）：

```json
{
  "presets": {
    "vocational_base": {
      "level": "vocational",
      "entity_types": ["岗位", "设备", "工具", "流程", "操作步骤", "规范", "安全标准", "物料"],
      "relation_types": ["操作", "规范", "上岗", "处置", "包含"]
    },
    "professional_base": {
      "entity_types": ["设备", "流程", "规范", "岗位", "工具", "材料"],
      "relation_types": ["操作", "规范", "使用", "负责"]
    },
    "my_course": {
      "extends": ["vocational_base", "professional_base"],
      "level": "vocational",
      "doc_type": "教材",
      "description": "手填/固化的课程预设示例",
      "entity_types": ["安检设备", "违禁品", "岗位", "操作流程", "法规", "技术标准", "突发事件", "旅客", "安全等级"],
      "relation_types": ["操作", "规范", "处置", "识别", "上报"]
    }
  },
  "routes": [
    { "match": "(?i)安检|民航", "preset": "my_course" }
  ]
}
```

- **展开规则**：预设 = 自身类型 + 全部 `extends` 祖先类型（去重保序，自身在前）；
- **路由**：`routes` 的 `match` 是正则，匹配文件相对路径（`file_id`）；未命中走 `--type-preset` 或 `default`；
- **模式**：`--type-mode strict`（只允许预设类型，无法归类的省略）`| loose`（预设为主、允许少量补充，默认）；
- **定制**：主路径 = `suggest_types.py` 模型按文档类型×学段×内容提议 → 用户确认 → `--apply` 固化；备选 = 用户手填 `type_presets.json` 或 `--schema`。内置仅领域骨架，不做课程默认。

## 输出格式（normalize 之后）

```json
{
  "entities": [
    {
      "id": "3064bf7c...",
      "text": "食品安全法",
      "label": "法律",
      "normalized_name": "食品安全法",
      "description": "《中华人民共和国食品安全法》是为了保证食品安全，保障公众身体健康和生命安全而制定的法律。",
      "attributes": [{"text": "2021年修正", "label": "版本"}]
    }
  ],
  "relations": [
    {
      "id": "3fe6ee2c...",
      "source_id": "6e2abcf0...",
      "target_id": "3064bf7c...",
      "source_text": "市场监管总局",
      "target_text": "食品安全法",
      "text": "负责执行",
      "label": "执行",
      "sources": ["docs/法规.md#part1", "docs/法规.md#part2"]
    }
  ],
  "metadata": {"schema_version": 1, "kb_id": "default", "entity_count": 2, "relation_count": 1}
}
```

`sources`：来源 chunk 数组（文件级为 `文件路径`，分块后为 `文件路径#partN`）。跨块合并取并集；增量删除时按 `sources` 前缀匹配定位，共享关系不误删。

`description`（实体字段）：**原文摘录**——抽取时模型必须从输入文本中逐字摘录"描述该实体"的原句（禁止总结/改写，无原句则空字符串）；跨块合并取最长非空摘录。可用于图谱渲染节点详情、GraphRAG 检索上下文。

## Postgres 落库（sync_graph_pg.py 自动建表）

```sql
CREATE TABLE graph_entities (
  kb_id text NOT NULL, entity_id text NOT NULL,
  text text NOT NULL, label text NOT NULL DEFAULT 'Entity',
  normalized_name text NOT NULL, description text NOT NULL DEFAULT '',
  attributes jsonb NOT NULL DEFAULT '[]',
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kb_id, entity_id)
);
CREATE TABLE graph_triples (
  kb_id text NOT NULL, triple_id text NOT NULL,
  source_id text NOT NULL, target_id text NOT NULL,
  source_text text NOT NULL, target_text text NOT NULL,
  text text NOT NULL, label text NOT NULL DEFAULT 'RELATED_TO',
  sources jsonb NOT NULL DEFAULT '[]',
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kb_id, triple_id)
);
CREATE INDEX idx_triples_source ON graph_triples (kb_id, source_id);
CREATE INDEX idx_triples_target ON graph_triples (kb_id, target_id);
```

对**存量库**，sync 会自动执行 `ALTER TABLE graph_entities ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT ''` 完成迁移（新库建表时已含该列）。

写入为 `UPSERT ON CONFLICT`，确定性 ID 保证幂等。增量删除按 `sources` 前缀匹配（`s = file_id OR s LIKE file_id || '#%'`），随后回收无任何关系引用的孤立实体（`NOT EXISTS` 子查询）。全库重建为 `DELETE WHERE kb_id` 后重写。

## 增量 vs 全量（门禁语义）

| 模式 | 行为 | 何时用 |
|---|---|---|
| `full` | 清空该 kb 全部重建 | 首次建库、数据源大改、用户明确要求全量 |
| `incremental` | diff 后：新增→抽+UPSERT；修改→删旧重抽覆盖；删除→按 sources 清理 | 日常新增/删改文件 |

`incremental` 要求 `--tracked` 状态文件（`{file_id: {filename, mtime, size}}`）；tracked 文件不要放在被扫描目录内。

## 与前端渲染解耦

Postgres 两表 JOIN 出 `{nodes: entities, edges: triples}` 即可喂给 G6 / Cytoscape / D3。大图用 `WHERE kb_id=$1 AND (source_id IN (子图实体集) OR target_id IN (子图实体集))` 只拉局部子图。换存储层（如将来上 Neo4j/Kuzu）时前端零改动。
