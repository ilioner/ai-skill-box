# Agent 直抽模式（默认路径，零配置）

本文件供 **Agent（当前运行的模型）直接执行抽取/类型提议**时使用：认知工作由 Agent 完成，
脚本只做确定性处理（规范化、合并、diff、落库）。**不需要配置任何 api-key / base-url / model**。

批处理模式（`--api-key`）仅在文档量大、Agent 上下文装不下，或需要独立跑脚本时使用（见 SKILL.md 快速开始"路径 B"）。

## 一、类型确认（门禁 1）

1. Agent 读取 `references/prompt-and-schema.md` 的类型预设说明与 `scripts/suggest_types.py` 中的
   提议 prompt（`SUGGEST_PROMPT`），基于文档的 **文档类型（教材/法规/技术文档/规章制度）× 学段 × 内容**
   提议实体/关系类型，并给出 `extends` 基类建议；
2. 展示给用户确认或修改；
3. 用户确认后，Agent 把类型写成文件（格式与模型输出一致）：

```json
{
  "doc_type": "教材",
  "level": "vocational",
  "extends": ["vocational_base", "professional_base"],
  "entity_types": [{"name": "安检设备", "reason": "..."}, {"name": "违禁品", "reason": "..."}],
  "relation_types": [{"name": "操作", "reason": "..."}, {"name": "规范", "reason": "..."}]
}
```

4. 固化：`python3 scripts/suggest_types.py --types-json types.json --preset-name my_course --apply`

## 二、抽取（Agent 直接产出 raw JSON）

Agent 读取 `references/prompt-and-schema.md` 的默认 prompt 与 Schema 约束，对每个文件（或分块）抽取实体关系，
**产出 raw JSON**（`{"relations": [{"source": {...}, "target": {...}, "text": "...", "label": "..."}]}`）。
要点：

- `description` 字段必须逐字摘录原文（见 prompt 规则），禁止总结改写；
- 分块时块名沿用 `file_id#partN` 规则（对应增量删除的前缀匹配）；
- 每个文件产出一份 raw；多个文件写成 JSONL：每行 `{"source": "文件相对路径", "extraction": {...raw...}}`。

## 三、规范化与合并（确定性脚本）

```bash
# 多文件：JSONL（每行 source + extraction）一次性规范化并合并
python3 scripts/normalize_graph.py --merge --input raws.jsonl --kb-id kb1 --output graph.json

# 或每份 raw 一个文件，逐个指定并合并
python3 scripts/normalize_graph.py --merge --input a.json --input b.json --kb-id kb1 --output graph.json
```

输出：去重合并后的 `{entities, relations}`，含确定性 ID、sources、description。

## 四、落库（门禁 2：先问新增/全量，--apply 才写）

```bash
python3 scripts/sync_graph_pg.py --mode full --files-dir docs/ --kb-id kb1 \
    --dsn postgres://... --type-preset my_course --type-mode loose --apply
```

全量/增量门禁与批处理模式完全一致（`--mode` 必填、`--apply` 才写库、增量需 `--tracked`）。
