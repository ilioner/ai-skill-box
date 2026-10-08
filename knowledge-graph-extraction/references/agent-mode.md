# Agent 直抽模式（唯一路径，零配置）

本文件供 **Agent（当前运行的模型）直接执行抽取 / 类型提议**时使用：认知工作由 Agent 完成，

脚本只做确定性处理（规范化、合并、diff、落库、固化）。**全程不需要配置任何 api-key /base-url/model。**

## 一、类型确认（门禁 1）



1. Agent 按 `references/prompt-and-schema.md` 的 "类型建议模板"，基于文档的

   **文档类型（教材 / 法规 / 技术文档 / 规章制度）× 学段 × 内容**提议实体 / 关系类型，并给出 `extends` 基类建议；

2. 展示给用户确认或修改；

3. 用户确认后，Agent 把类型写成 `types.json`：



```
{

&#x20; "doc\_type": "教材",

&#x20; "level": "vocational",

&#x20; "extends": \["vocational\_base", "professional\_base"],

&#x20; "entity\_types": \[{"name": "安检设备", "reason": "..."}, {"name": "违禁品", "reason": "..."}],

&#x20; "relation\_types": \[{"name": "操作", "reason": "..."}, {"name": "规范", "reason": "..."}]

}
```



1. 固化：`python3 scripts/suggest_types.py --types-json types.json --preset-name my_course --apply`

## 二、抽取（Agent 直接产出 raw JSON）

Agent 按 `references/prompt-and-schema.md` 的默认 prompt 与类型预设展开的 Schema 约束，

对每个文件（或分块）抽取实体关系，**产出 raw JSON**

（`{"relations": [{"source": {...}, "target": {...}, "text": "...", "label": "..."}]}`）。要点：



* `description` 字段必须逐字摘录原文（见 prompt 规则），禁止总结改写；

* 文档中的设问、承接、邀请阅读和“知识链接”导读语只作为后续知识块的结构提示，不单独抽取；应识别其指向的规范知识主题，将连续的阶段/组成部分统一挂到该理论框架，并保留框架与正文核心概念的解释关系；

* 分块时块名沿用 `file_id#partN` 规则（对应增量删除的前缀匹配）；

* 每个文件产出一份 raw；多个文件写成 JSONL：每行 `{"source": "文件相对路径", "extraction": {...raw...}}`。

## 三、规范化与合并（确定性脚本）



```
\# 多文件：JSONL（每行 source + extraction）一次性规范化并合并

python3 scripts/normalize\_graph.py --merge --input raws.jsonl --kb-id kb1 --output graph.base.json

\# 或每份 raw 一个文件，逐个指定并合并

python3 scripts/normalize\_graph.py --merge --input a.json --input b.json --kb-id kb1 --output graph.base.json
```

输出：去重合并后的 `{entities, relations}`，含确定性 ID、sources、description。

### 教材/课程的最终扩展字段

上述规范化输出只是基础图谱。教材/课程将它保存为 `graph.base.json`，再按 `references/entity-codes.md` 核对教材业务编码与 `location_map.json`，执行：

```bash
python3 scripts/enrich_graph_codes.py --graph graph.base.json \
    --locations location_map.json --book-code MYCOURSE --output graph.json
```

最终实体增加 `code/book_code/chapter_no/section_no/knowledge_no/location_codes`，元数据增加 `entity_code_scheme`。增量用旧 `graph.json` 作 `--existing` 基线，不能先覆盖旧文件；缺少目录定位时补齐依据，不由 Agent 编造序号。实体 `id` 与关系不变。此 JSON 扩展不代表数据库已保存这些字段。

## 四、落库（门禁 2：先问新增 / 全量，--apply 才写）



```
\# 先问用户"新增 or 全量"，再执行

python3 scripts/sync\_graph\_pg.py --mode full --graph graph.json --kb-id kb1 \\

&#x20;   \--dsn postgres://... --apply

python3 scripts/sync\_graph\_pg.py --mode incremental --graph graph.json --kb-id kb1 \\

&#x20;   \--dsn postgres://... \[--apply]
```

门禁：`--mode` 必填（full/incremental）、`--apply` 才写库。增量删除的基线在库内

（库内 sources 前缀 vs graph.json 的 sources 前缀），无需 tracked 文件。
