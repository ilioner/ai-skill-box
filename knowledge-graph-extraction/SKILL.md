***

name: knowledge-graph-extraction

description: 从文本 / 文档块中抽取实体 - 关系三元组，输出规范化、带确定性 ID 与来源追踪的图谱 JSON，并支持同步到 Postgres（全量 / 增量）。支持 "类型建议门禁"：抽取前先由模型基于文档提议节点 / 关系类型（按学段 × 学科 × 课程预设，type\_presets.json）供用户确认。用于知识图谱构建、GraphRAG 数据准备、实体关系抽取、图谱落库（Postgres/Neo4j/Milvus）前的数据清洗合并、图谱增量更新。触发场景：抽取实体关系、构建知识图谱数据、三元组抽取、图谱入库、图谱落库、图谱增量更新、图谱同步 Postgres、节点类型 / 实体类型设置、类型预设。



***

# 知识图谱数据抽取与同步

把非结构化文本变成结构化图谱数据：**类型建议门禁**（模型提议类型 → 用户确认）→ LLM 抽取实体与关系 → 容错解析 → 规范化去重合并 → 输出带确定性 ID 与来源追踪（sources）的 `{entities, relations}` JSON → 可选同步到 Postgres（全量重建 / 增量更新）。

## ⛔ 运行门禁（强制，违反即违规）

**门禁 1 —— 类型建议门禁（抽取前第一步）**：

类型来源两条路径，任选其一，都必须在抽取前完成确认：



* **路径 A（模型提议，主路径）**：运行 `scripts/suggest_types.py`，模型根据文档类型（教材 / 法规 / 技术文档 / 规章制度）× 学段 × 抽样内容自行提议实体 / 关系类型 → 展示给用户确认或修改 → 确认后加 `--apply` 固化到 `type_presets.json`。

* **路径 B（用户手填）**：用户直接编辑 `type_presets.json` 新增课程预设，或用 `--schema` 手填类型。

规则：



1. 未得到用户对类型的明确确认 → 禁止进入抽取；不得用默认类型静默代替。

2. 已有确认过的预设并显式指定 `--type-preset`/`--schema` 时，可跳过提议步骤，但须向用户说明将使用哪个预设。

3. 脚本层机械门禁：`suggest_types.py` 不加 `--apply` 绝不写盘；`--dry-run` 不调模型不写盘。

**门禁 2 —— 新增 / 全量门禁（抽取 / 入库前）**：



1. 未得到用户明确答复 → 禁止运行任何脚本，不得默认选择、不得猜测。

2. 用户答 "新增 / 增量" → 只能运行 `--mode incremental`；答 "全量 / 重建" → 只能运行 `--mode full`。两者混用即违规。

3. 脚本层机械门禁（双保险）：`--mode` 必填无默认值；不加 `--apply` 只打印计划绝不写库；增量模式缺 `--tracked` 直接拒绝。

4. 首次建立图谱必须走全量（incremental 无 tracked 会被拒绝）。

## 类型预设（type\_presets.json）

`type_presets.json`（技能根目录）**只内置 "领域骨架"**，不内置任何课程示例：



* **兜底**：`default`（Entity / RELATED\_TO）；

* **学段基类**：`k12_base`（知识点 / 考点导向）、`undergrad_base`（理论 / 原理导向）、`vocational_base`（岗位 / 流程 / 规范导向）；

* **学科基类**：`science_base`（理科）、`humanities_base`（文科）、`professional_base`（职业实务）。

这些基类只是给 `suggest_types.py` 提供 `extends` 候选（领域知识骨架），**课程级类型不内置**，由以下两条路径产生（见门禁 1）：



* **模型提议（主路径）**：`suggest_types.py` 按文档类型 × 学段 × 内容提议 → 用户确认 → `--apply` 固化，生成带 `extends/level/doc_type` 的课程预设；

* **用户手填**：直接在 `type_presets.json` 加一条 `{"extends": [...], "entity_types": [...], "relation_types": [...]}`，或用 `--schema` 手填。

展开规则：预设 = 自身类型 + 全部 `extends` 祖先类型（去重保序）。`routes` 数组支持按文件路径正则路由（`{match, preset}`），手填或固化后可自行维护。

## 快速开始



```
\# 0) 类型建议门禁（路径 A：模型提议；不写盘）

python3 scripts/suggest\_types.py --files-dir docs/ --course "民航安检服务" \\

&#x20;   \--api-key "\$OPENAI\_API\_KEY"

\#    确认后固化（写 type\_presets.json 新增课程预设）

python3 scripts/suggest\_types.py --files-dir docs/ --course "民航安检服务" \\

&#x20;   \--preset-name my\_course --extends vocational\_base,professional\_base \\

&#x20;   \--api-key "\$OPENAI\_API\_KEY" --apply

\#    路径 B（用户手填）：编辑 type\_presets.json 加预设，或直接用 --schema 手填类型

\# 1) 纯抽取（用确认过的预设；先问用户"新增还是全量"，纯抽取不落库可不问）

python3 scripts/extract\_graph.py --input docs/ --output graph.json \\

&#x20;   \--model gpt-4o-mini --api-key "\$OPENAI\_API\_KEY" \\

&#x20;   \--type-preset my\_course --type-mode loose

\# 2) 首次全量入库（先问用户确认"全量"，再执行）

python3 scripts/sync\_graph\_pg.py --mode full --files-dir docs/ --kb-id kb1 \\

&#x20;   \--dsn postgres://user:pass@localhost:5432/db \\

&#x20;   \--model gpt-4o-mini --api-key "\$OPENAI\_API\_KEY" \\

&#x20;   \--type-preset my\_course --type-mode loose --apply

\# 3) 后续增量（先问用户确认"新增"，先看计划再 --apply）

python3 scripts/sync\_graph\_pg.py --mode incremental --files-dir docs/ \\

&#x20;   \--tracked tracked.json --kb-id kb1 --dsn postgres://... \\

&#x20;   \--type-map type\_map.json                # 打印计划（含类型路由）；type\_map.json 可自建

python3 scripts/sync\_graph\_pg.py --mode incremental --files-dir docs/ \\

&#x20;   \--tracked tracked.json --kb-id kb1 --dsn postgres://... \\

&#x20;   \--type-map type\_map.json --apply        # 执行
```

`type_map.json` 为自定义路由文件（`{"routes": [{"match": "安检|民航", "preset": "my_course"}], "default": "..."}`）；也可把 `routes` 直接维护在 `type_presets.json` 里。

环境变量：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`GRAPH_EXTRACTION_MODEL`；API 为 OpenAI 兼容 `/chat/completions`。Postgres 驱动：`pip install psycopg[binary]`（或 psycopg2）。

## 工作流



1. **门禁 1 类型确认**（见上）：模型提议（`suggest_types.py`）或用户手填 → 用户确认 → 固化或指定预设。

2. **门禁 2 模式确认**（见上）：确认 "新增 or 全量" 后才可继续。

3. **抽取**：输入（文本 / 目录 / JSONL/JSON/stdin）→ 分块（`--max-chars`，块名 `file#partN`）→ 并发 LLM 抽取 → 容错解析 → 规范化（实体按规范化名 + label 去重合并、属性并集、确定性 ID）。

4. **类型约束**：`--schema`（显式，优先）或 `--type-preset`（预设展开）；`--type-mode strict|loose` 控制是否允许 LLM 补充类型。

5. **来源追踪**：每条关系带 `sources` 数组（来源文件 /chunk），跨块合并取并集 —— 增量删除 / 覆盖时按来源精确定位，共享关系不会被误删。

6. **同步 Postgres**（`sync_graph_pg.py`）：

* `full`：清空该 kb 重建（幂等安全）；

* `incremental`：diff tracked vs current → 新增 = 抽取 + UPSERT；修改 = 删旧 + 重抽 + 覆盖；删除 = 按 sources 清理三元组 + 回收孤立实体（纯 SQL 无 AI）。整体单事务，失败回滚。

* 类型路由：`--type-map` 按文件路径正则分配预设（`routes`），未命中用 `--type-preset`/`default`；plan 模式打印 "类型路由：预设 × 文件数" 摘要供确认。

## 关键脚本



* `scripts/suggest_types.py` — **类型建议门禁**：模型抽样提议实体 / 关系类型 → 打印建议 → `--apply` 固化到 type\_presets.json（`--dry-run` 不调模型不写盘）。

* `scripts/extract_graph.py` — 端到端抽取（含来源追踪、类型预设展开、strict/loose）。

* `scripts/normalize_graph.py` — 纯规范化工具（`--source` 可手动标注来源）。

* `scripts/sync_graph_pg.py` — 图谱同步 Postgres：门禁（--mode 必填 /--apply 才写库）+ diff 增量 / 全量 + UPSERT / 清理 + 类型路由，自动建表。

* `type_presets.json` — 类型预设表（学段 × 学科 × 课程，extends 继承 + routes 路由），可配置可扩展。

## 输出格式与落库

详见 `references/prompt-and-schema.md`：默认 prompt、schema 约束、类型预设展开示例、输出 JSON 结构（含 sources；实体含 `description` 原文摘录，非模型总结）、Postgres 表结构（graph\_entities /graph\_triples，sources jsonb 列，description 列自动迁移）与前端渲染映射。

常用调整：实体太泛 → 收紧预设类型或 `--schema`；噪声多 → `--type-mode strict`；更细粒度 → 调小 `--max-chars`；新课程 → 跑 `suggest_types.py` 定制预设。