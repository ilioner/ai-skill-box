---
name: knowledge-graph-extraction
description: 从文本/文档块中抽取实体-关系三元组，输出规范化、带确定性 ID 与来源追踪的图谱 JSON，支持同步到 Postgres（全量/增量），并可一键渲染成交互式 2D/3D 知识图谱 HTML。全程由 Agent 直接抽取（零配置，无需 api-key），脚本只做规范化/合并/落库/渲染等确定性工作。带"类型建议门禁"（Agent 按文档类型×学段×内容提议节点类型，用户确认后固化进 type_presets.json）。用于知识图谱构建、GraphRAG 数据准备、实体关系抽取、图谱落库（Postgres/Neo4j/Milvus）前的数据清洗合并、图谱增量更新、图谱可视化。触发场景：抽取实体关系、构建知识图谱数据、三元组抽取、图谱入库、图谱落库、图谱增量更新、图谱同步 Postgres、节点类型/实体类型设置、类型预设、图谱可视化、渲染知识图谱、生成图谱网页、graph.json 转 HTML、2D/3D 图谱。
---

# 知识图谱数据抽取与同步

把非结构化文本变成结构化图谱数据：**类型建议门禁**（类型确认）→ Agent 抽取实体与关系 → 规范化去重合并 → 输出带确定性 ID 与来源追踪（sources）的 `{entities, relations}` JSON → 可选同步到 Postgres（全量重建 / 增量更新）。

**执行方式（唯一路径 · Agent 直抽 · 零配置）**：本 skill 运行在智能体环境中，Agent 本身即模型——类型提议、实体关系抽取这些认知工作由 Agent 直接完成（读取 `references/prompt-and-schema.md` 与 `references/agent-mode.md` 的模板），脚本只做**确定性工作**（规范化、合并、diff、落库、固化）。**不需要配置任何 api-key / base-url / model。**

## ⛔ 运行门禁（强制，违反即违规）

**门禁 1 —— 类型建议门禁（抽取前第一步）**：

类型来源两条路径，任选其一，都必须在抽取前完成确认：

- **路径 A1（Agent 直提，默认）**：Agent 按模板基于文档类型（教材/法规/技术文档/规章制度）× 学段 × 内容提议类型 → 展示给用户确认或修改 → 用户确认后存为 types.json → `suggest_types.py --types-json types.json --apply` 固化。
- **路径 A2（用户手填）**：用户直接编辑 `type_presets.json` 新增课程预设。

规则：
1. 未得到用户对类型的明确确认 → 禁止进入抽取；不得用默认类型静默代替。
2. 已有确认过的预设并显式指定时，可跳过提议步骤，但须向用户说明将使用哪个预设。
3. 脚本层机械门禁：`suggest_types.py` 不加 `--apply` 绝不写盘；`--dry-run` 不写盘。

**门禁 2 —— 新增/全量门禁（抽取/入库前）**：

1. 未得到用户明确答复 → 禁止运行任何脚本，不得默认选择、不得猜测。
2. 用户答"新增/增量" → 只能运行 `--mode incremental`；答"全量/重建" → 只能运行 `--mode full`。两者混用即违规。
3. 脚本层机械门禁（双保险）：`--mode` 必填无默认值；不加 `--apply` 只打印计划绝不写库。
4. 首次建立图谱必须走全量。

## 类型预设（type_presets.json）

`type_presets.json`（技能根目录）**只内置"领域骨架"**，不内置任何课程示例：

- **兜底**：`default`（Entity / RELATED_TO）；
- **学段基类**：`k12_base`（知识点/考点导向）、`undergrad_base`（理论/原理导向）、`vocational_base`（岗位/流程/规范导向）；
- **学科基类**：`science_base`（理科）、`humanities_base`（文科）、`professional_base`（职业实务）。

这些基类只作为类型提议的 `extends` 候选（领域知识骨架），**课程级类型不内置**，由门禁 1 产生。展开规则：预设 = 自身类型 + 全部 `extends` 祖先类型（去重保序）。`routes` 数组支持按文件路径正则路由（`{match, preset}`）。

## 工作流

1. **门禁 1 类型确认**（见上）：Agent 直提 / 用户手填 → 用户确认 → 固化或直接使用。
2. **门禁 2 模式确认**（见上）：确认"新增 or 全量"后才可继续。
3. **抽取**（Agent 完成）：Agent 按 `references/prompt-and-schema.md` 的模板逐文件抽取，产出 raw JSON（每行 `{"source": "文件相对路径", "extraction": {...}}` 写入 raws.jsonl）。要点：`description` 逐字摘录原文；分块名沿用 `file_id#partN`。
4. **规范化合并**（脚本）：`normalize_graph.py --merge` 容错解析 → 规范化（实体按规范化名+label 去重合并、属性并集、description 最长非空、确定性 ID）→ 跨文件合并（sources 并集）→ 输出 `graph.json`。
5. **同步 Postgres**（脚本，`sync_graph_pg.py`）：
   - `full`：清空该 kb 重建（幂等安全）；
   - `incremental`：图级 diff——UPSERT graph.json 全部；删除库中存在但图中不再出现的来源前缀（按 sources 前缀清理三元组 + 回收孤立实体，纯 SQL 无 AI）。基线在库内，无需 tracked 文件。整体单事务，失败回滚。
6. **可视化渲染**（脚本，可选）：`render_graph.py` / `render_graph_3d.py` 直接吃 `graph.json` 出交互式 HTML。**与落库无关、可独立执行**，无门禁（只读输入 + 只写 HTML 产物）。2D 默认内联 ECharts → 离线可开；3D 运行时依赖 CDN。详见 `references/rendering.md`。

## 快速开始

```text
1. 门禁1 类型确认：Agent 按 references/prompt-and-schema.md 模板提议类型 → 用户确认 → 存 types.json
   → python3 scripts/suggest_types.py --types-json types.json --preset-name my_course --apply
2. Agent 按 references/prompt-and-schema.md 模板逐文件抽取，产出 raws.jsonl
   （每行 {"source": "文件相对路径", "extraction": {...}}）
3. 规范化合并：python3 scripts/normalize_graph.py --merge --input raws.jsonl \
       --kb-id kb1 --output graph.json
4. 门禁2 问"新增 or 全量" → sync_graph_pg.py --mode <full|incremental> \
       --graph graph.json --kb-id kb1 --dsn postgres://... [--apply]
5. 可视化（可选，无门禁）：python3 scripts/render_graph.py \
       --graph graph.json --output graph.html --title "项目知识图谱"
```

Postgres 驱动：`pip install psycopg[binary]`（或 psycopg2）。

## 关键脚本

- `scripts/suggest_types.py` — 类型门禁脚本（确定性）：读取 Agent 提议、用户确认后的 `--types-json`，打印/固化到 type_presets.json；`--apply` 才写盘，`--dry-run` 不写盘。
- `scripts/normalize_graph.py` — 纯确定性：容错解析 + 规范化 + `--merge` 多来源合并（Agent 直抽的落点）。
- `scripts/sync_graph_pg.py` — 图谱同步 Postgres（纯写库，无 LLM）：输入 `--graph`（Agent 产物），门禁（--mode 必填 / --apply 才写库）+ 全量重建/图级 diff 增量 + UPSERT/清理，自动建表与 description 列迁移。
- `scripts/render_graph.py` — 2D 渲染（ECharts，纯确定性）：`graph.json` → 单文件 HTML。默认自动探测并内联 `assets/echarts.min.js`（零 CDN 依赖、离线可开），`--rel-colors` 可注入项目配色，`--path-json` 接学习路径导览面板。
- `scripts/render_graph_3d.py` — 3D 渲染（3d-force-graph）：参数只有 `--graph/--output/--title/--path-json`；运行时依赖 CDN，离线打不开。
- `type_presets.json` — 类型预设表（领域骨架，extends 继承 + routes 路由），可配置可扩展。
- `assets/echarts.min.js` — 2D 渲染内联用的 ECharts 运行时（渲染器自动探测，无需手动指定）。

## 输出格式与落库

Agent 直抽流程详见 `references/agent-mode.md`；类型建议模板、默认抽取 prompt、Schema 约束、输出 JSON 结构（含 sources、description）、Postgres 表结构（graph_entities / graph_triples，sources jsonb 列，description 列自动迁移）与前端渲染映射见 `references/prompt-and-schema.md`。

可视化渲染的输入契约（`graph.json` / `learning_path.json` 字段结构、单元下钻靠实体 `attributes` 里的 `unitId`/`lessonId` 关联）、ECharts 资源探测顺序、配色覆盖、关系标签隐形节点 `opacity:1` 踩坑点见 `references/rendering.md`。**产物 HTML 不可手改**——改动一律回到渲染脚本，否则重跑即丢。

常用调整：实体太泛 → 收紧预设类型；噪声多 → 类型约束模式改 strict；更细粒度 → 分块更小；新课程 → 走门禁 1 定制预设。
