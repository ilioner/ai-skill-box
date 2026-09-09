# 抽取 Prompt、Schema 与 Postgres 落库参考（Agent 直抽版）

本文件是 **Agent 直抽模式**的操作依据：Agent 读取这里面的模板与约束直接执行抽取，脚本只做确定性工作。**全程不需要 api-key / base-url / model 配置。**

## 一、类型建议模板（门禁 1：抽取前第一步）

Agent 按以下模板，基于文档的**文档类型 × 学段 × 内容**提议实体/关系类型，展示给用户确认或修改；用户确认后写成 `types.json`，用 `suggest_types.py --types-json types.json --apply` 固化进 `type_presets.json`。

```text
你是知识图谱类型体系设计专家。下面是文档集《{course}》的抽样内容（{sample_note}）。
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
- 类型要贴合该文档集的实际内容（文档类型、学段、课程主题），能覆盖主要知识单元，
  类型之间尽量正交、不重叠；
- 输出严格 JSON（不要输出解释）：
{
  "doc_type": "教材|法规|技术文档|规章制度",
  "level": "k12|undergrad|vocational",
  "extends": ["基类名1", "基类名2"],
  "entity_types": [{"name": "类型名", "reason": "一句话理由"}],
  "relation_types": [{"name": "类型名", "reason": "一句话理由"}]
}

抽样内容开始：
{sample}
抽样内容结束。
```

模板占位符说明：`{course}`=文档集名、`{sample_note}`=文件数与抽样数、`{dirs}`=文件/目录线索（前 20 个）、`{level_hint}`=学段提示（auto/k12/undergrad/vocational）、`{base_names}`=`type_presets.json` 中可继承的基类名、`{sample}`=抽样文本（每文件前约 1500 字符，总量约 8000 字符）。

用户确认后 `types.json` 的格式即模板输出格式。固化：`python3 scripts/suggest_types.py --types-json types.json --preset-name my_course --apply`。

## 二、默认抽取 Prompt（Agent 直抽时使用）

要求 Agent 返回严格 JSON，只含 `relations` 数组（source/target 内嵌实体对象）：

```json
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
```

要点：
- `entities` 顶层数组可省略——规范化会自动从 relations 的 source/target 补全实体；
- 关系 `text` 必填，`label` 缺省为 `RELATED_TO`；
- 关系端点可引用实体数组中的 `text` 或 `id`（字符串），也可直接内嵌对象；
- **`description` 必须逐字摘录原文**（见下"描述字段"）。

## 三、Schema 约束（来自类型预设）

抽取时用 `type_presets.json` 中已确认预设展开的类型约束，附加在默认 prompt 之后：

```text
实体类型: 法规/机构/产品/人物
关系类型: 制定/执行/违反/引用
```

## 四、类型预设（type_presets.json）

技能根目录的 `type_presets.json` **只内置领域骨架**（`default` 兜底 + 学段基类 + 学科基类），课程级预设由 Agent 按上文模板提议、用户确认后固化，或用户手填。手填格式示例（写入 `presets` 字段）：

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
- **路由**：`routes` 的 `match` 是正则，匹配文件相对路径（`file_id`）；未命中走 `default` 或 Agent 指定的预设；
- **类型约束模式**：`strict`（只允许预设类型，无法归类的省略）`| loose`（预设为主、允许少量补充，默认）；
- **定制**：主路径 = Agent 按模板提议 → 用户确认 → `suggest_types.py --apply` 固化；备选 = 用户手填 `type_presets.json`。内置仅领域骨架，不做课程默认。

## 五、输出格式（normalize 之后）

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

**描述字段**（实体 `description`）：**原文摘录**——抽取时必须从输入文本中逐字摘录"描述该实体"的原句（禁止总结/改写，无原句则空字符串）；跨块合并取最长非空摘录。可用于图谱渲染节点详情、GraphRAG 检索上下文。

## 六、Postgres 落库（sync_graph_pg.py 自动建表）

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

写入为 `UPSERT ON CONFLICT`，确定性 ID 保证幂等。

## 七、增量 vs 全量（门禁语义）

| 模式 | 行为 | 何时用 |
|---|---|---|
| `full` | 清空该 kb 全部重建（写入 `--graph` 的内容） | 首次建库、数据源大改、用户明确要求全量 |
| `incremental` | 图级 diff：UPSERT `--graph` 全部；删除库中存在但图中不再出现的来源前缀（按 sources 前缀匹配 + 回收孤立实体） | 日常新增/删改文件（Agent 重新产出全量 graph.json 即可） |

增量删除的**基线在库内**（`graph_triples.sources` 与 graph.json 的 sources 前缀对比），无需 tracked 状态文件。删除逻辑纯 SQL 无 AI；整体单事务，失败回滚。

## 八、与前端渲染解耦

Postgres 两表 JOIN 出 `{nodes: entities, edges: triples}` 即可喂给 G6 / Cytoscape / D3。大图用 `WHERE kb_id=$1 AND (source_id IN (子图实体集) OR target_id IN (子图实体集))` 只拉局部子图。换存储层（如将来上 Neo4j/Kuzu）时前端零改动。
