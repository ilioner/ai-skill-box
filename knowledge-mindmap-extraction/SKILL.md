---
name: knowledge-mindmap-extraction
description: 把文件/文档清单组织成层级知识导图 JSON 树：全量生成 + 增量维护（diff 检测、删除走纯树手术无 AI、新增走整合），带强制运行门禁（--mode 必填、--apply 才写文件）。双路径：默认由 Agent 直接生成/整合树（零配置，--tree-json），脚本只做校验/写文件/追踪；可选 --api-key 批处理。用于知识库导图、目录概览、文件分类树、学习资料组织、知识结构可视化前的数据准备。触发场景：生成思维导图、知识导图、文件分类树、目录概览、知识库结构整理、导图增量更新。
---

# 知识导图数据抽取

把"文件清单"变成一棵层级树（`{content, children}`），供 markmap / ECharts / 前端图组件渲染。核心设计：**全量生成靠模型，增量维护尽量不靠模型**——删除文件走纯树手术，只有新增文件才需要整合。

## 双路径（树的生成/整合方式）

- **路径 A · Agent 直抽（默认，零配置）**：Agent 本身即模型，读取 `references/prompts.md` 的模板，直接生成全量树（或对新增文件做增量整合、对删除做剪枝），产出完整树 JSON → `generate_mindmap.py --tree-json tree.json` 只做校验、写文件、更新追踪映射。**不需要配置 api-key / base-url / model。**
- **路径 B · 批处理（可选）**：`generate_mindmap.py --api-key` 由外部模型生成/整合。缺 key 时会提示切回路径 A。

## ⛔ 运行门禁（强制，违反即违规）

**任何生成/更新动作前，必须先询问用户本次是「新增」还是「全量」**：

1. 未得到用户明确答复 → 禁止运行任何脚本，不得默认选择、不得猜测。
2. 用户答"新增/增量" → 只能运行 `--mode incremental`；答"全量/重建" → 只能运行 `--mode full`。两者混用即违规。
3. 脚本层机械门禁（双保险）：`--mode` 必填无默认值；不加 `--apply` 只打印计划，不写文件（路径 A 亦不读树）；增量模式缺 `--existing` 直接拒绝。
4. 首次生成必须走全量（incremental 无 `--existing` 会被拒绝）。

## 快速开始

**路径 A（默认 · Agent 直抽 · 零配置）**：

```text
1. 门禁问"新增 or 全量"
2. Agent 读取 references/prompts.md 模板：
   - full：基于文件清单生成 2-4 层树（根=库名、一级=分类、叶子=文件名且全树唯一）
   - incremental：对新增文件做增量整合（保留原结构），对删除做剪枝
3. 产出完整树 tree.json（{content, children}）
4. python3 scripts/generate_mindmap.py --mode <full|incremental> \
       --tree-json tree.json --files-dir docs/ --output mindmap.json --apply
```

**路径 B（批处理 · --api-key）**：

```bash
# 1) 全量生成（先问用户确认"全量"，再执行）
python3 scripts/generate_mindmap.py --mode full --files-dir docs/ \
    --root-name "产品资料库" --output mindmap.json \
    --model gpt-4o-mini --api-key "$OPENAI_API_KEY" --apply

# 2) 增量更新（先问用户确认"新增"，先看计划再 --apply）
python3 scripts/generate_mindmap.py --mode incremental --existing mindmap.json \
    --tracked tracked.json --files-json files.json                    # 打印计划
python3 scripts/generate_mindmap.py --mode incremental --existing mindmap.json \
    --tracked tracked.json --files-json files.json --api-key "$OPENAI_API_KEY" --apply

# 3) 校验 / 修剪 / 变更检测（确定性工具，两种路径通用）
python3 scripts/mindmap_tree.py --mode validate --input mindmap.json
python3 scripts/mindmap_tree.py --mode prune --input mindmap.json --remove "旧文件.pdf"
```

环境变量（仅路径 B 需要）：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`MINDMAP_MODEL`；API 为 OpenAI 兼容 `/chat/completions`。

## 工作流

**全量生成**（`--mode full`）：读文件清单（目录扫描 / JSON / JSONL，`--max-files` 默认 200）→ 生成树（路径 A：Agent 直抽，`--tree-json`；路径 B：`--api-key`）→ 校验 → 输出 `mindmap.json` + `tracked.json`（file_id → filename 追踪映射）。

**增量维护**（`--mode incremental`）：
1. `detect_changes` 对比 `tracked` 与当前文件清单 → `added / removed / needs_update`；
2. 无变更直接提示"已是最新"；
3. 路径 A：Agent 提供整合后的完整树（`--tree-json`），脚本校验 + 重建 tracked（删除自然消失）；
4. 路径 B：**删除**走 `remove_files` 纯树手术（无 AI）；**新增**才调 LLM 增量整合（保留原结构，新文件归入最合适分类，可新建分类）；
5. 输出更新后的树 + 追踪映射。

两种模式在 `--apply` 前都只打印计划；无变更时直接提示"已是最新"。

## 关键脚本

- `scripts/generate_mindmap.py` — 全量生成 + 增量更新（门禁：`--mode` 必填 / `--apply` 才写文件；`--tree-json` = Agent 直抽，`--api-key` = 批处理）。
- `scripts/mindmap_tree.py` — 树工具：`validate`（结构 + 叶子唯一性）、`prune`（删除）、`diff`（变更检测）、`load_files`（清单读取），可独立使用。

## 树格式与调优

树格式、全量/增量 prompt 细节、分类调优建议见 `references/prompts.md`（Agent 直抽时按其中模板执行）。渲染端：markmap（SVG）、ECharts tree/sunburst 均可直接消费该 JSON。
