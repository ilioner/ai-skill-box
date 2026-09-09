# 导图生成 Prompt 参考

## 运行门禁（与 SKILL.md 一致）

- `--mode` 必填：`full`=全量重建，`incremental`=增量更新；运行前必须先问用户选哪个。
- 不加 `--apply` 只打印计划（增量会先输出新增/删除清单），不调 LLM、不写文件。
- incremental 必须有 `--existing`（现有导图）与 `--tracked`（追踪映射）；首次用 `--mode full`。

## 全量生成（内置于 generate_mindmap.py）

系统提示要求模型输出 2-4 层 JSON 树：

```json
{
  "content": "知识库名称",
  "children": [
    {
      "content": "🎯 主分类1",
      "children": [
        {"content": "文件名1.txt", "children": []},
        {"content": "文件名2.pdf", "children": []}
      ]
    }
  ]
}
```

硬性约束（写在 prompt 中）：
- 根节点 = 知识库名称；
- 第一层为主分类（可用 emoji 增强可读性），第二层子分类；
- **叶子节点必须是文件名**，且每个文件名在整个树中**只能出现一次**；
- 一个文件可能属于多个分类时，只选最合适的唯一分类；
- 叶子节点的 `children` 必须是空数组 `[]`。

用户消息包含：知识库名、文件总数、文件清单（`- 文件名 (类型)` 每行一个）、可选用户补充说明。

## 增量整合（保留原结构 + 新文件）

系统提示要求：
- 保留现有分类结构不变，把新文件放进最合适的已有分类；
- 新文件不属于任何现有分类时，允许新建分类节点；
- 返回**完整**导图 JSON（原结构 + 新文件），而非只返回新增部分。

用户消息包含：现有导图 JSON（缩进打印）+ 新增文件清单 + 可选用户补充说明。

## 调优建议

| 问题 | 做法 |
|---|---|
| 分类太粗 / 太细 | 在 `--user-prompt` 里补充分类维度，如"按业务线分类，不超过 6 个一级分类" |
| 文件重复出现 | prompt 已强约束；仍出现时用 `mindmap_tree.py --mode validate` 检出并修剪 |
| 增量后结构漂移 | 增量 prompt 强调"保留现有结构"，必要时在 user-prompt 中声明哪些分类不可动 |
| 单次文件过多 | `--max-files` 限制（默认 200）；更多文件可分多次增量加入 |

## 输出与渲染

树 JSON 可直接给：
- **markmap**（markmap-lib Transformer + markmap-view）渲染 SVG 导图；
- **ECharts tree / sunburst** 等前端组件；
- 后端存储：知识库元数据字段（如 Postgres 的 `mindmap` JSON 列），配套 `tracked.json`（file_id → filename）做增量 diff。
