# Capability Graph Extractor

从课程标准、教案、课件等教学资料中提炼可追溯的能力图谱。

## 快速开始

### 最小化工作流（无脚本）

如果你只有少量材料（< 5 个文件，< 30 个能力节点），可以完全手工完成：

1. **准备材料**：收集课程标准、授课计划、教案等文件到一个目录
2. **读取 SKILL.md**：了解工作原则和输出结构
3. **阅读方法论**：`references/extraction-method.md` 了解如何识别和规范化能力
4. **手工提取**：按照方法论从材料中提取能力节点和关系
5. **编写 JSON**：参考 `templates/graph.schema.json` 手工编写图谱文件
6. **人工校验**：检查节点类型、关系端点、证据来源是否完整

**适用场景**：小型课程、快速原型、学习 Skill 方法论

### 标准工作流（使用脚本）

当材料较多或需要自动化校验时，使用提供的 Python 脚本：

```bash
# 1. 清点材料
python scripts/inventory_materials.py /path/to/materials/ -o output/material_inventory.json

# 2. 手工提取能力（参考 extraction-method.md）
# 编辑 output/graph.draft.json

# 3. 分配稳定 ID
python scripts/assign_stable_ids.py output/graph.draft.json -o output/graph.json

# 4. 校验图谱结构
python scripts/validate_graph.py output/graph.json --connectivity

# 5. 导出可视化
python scripts/export_graph_html.py output/graph.json -o output/

# 6. 导出 CSV 和 Mermaid
python scripts/export_formats.py output/graph.json -o output/
```

**适用场景**：正式课程开发、多人协作、需要质量报告

## 脚本使用决策树

### 何时使用脚本？

```
材料清点（inventory_materials.py）
├─ 材料 ≥ 10 个文件？ → 使用脚本
├─ 需要生成材料清单报告？ → 使用脚本
└─ 否则 → 手工列出文件即可

稳定 ID 分配（assign_stable_ids.py）
├─ 节点 ≥ 50 个？ → 使用脚本
├─ 需要跨版本追踪节点变化？ → 使用脚本
└─ 否则 → 手工编号（C001, C002...）

图谱校验（validate_graph.py）
├─ 需要检测孤立节点？ → 必须使用（新增功能）
├─ 需要检测多根节点？ → 必须使用（新增功能）
├─ 需要验证引用完整性？ → 使用脚本
├─ 需要生成质量报告？ → 使用脚本
└─ 否则 → 人工检查 JSON 格式

HTML 可视化（export_graph_html.py）
├─ 需要交互式图谱浏览？ → 必须使用（核心功能）
├─ 需要移动端响应式布局？ → 必须使用（v1.1 新增）
└─ 现在自动生成两个版本：
    ├─ graph_clean.html（极简展示版）
    └─ graph_full.html（完整分析版）

格式导出（export_formats.py）
├─ 需要导入数据库？ → 使用脚本生成 CSV
├─ 需要嵌入文档？ → 使用脚本生成 Mermaid
└─ 否则 → JSON 已足够
```

### 脚本依赖

所有脚本只依赖 Python 标准库，无需安装额外包：

```bash
python --version  # 需要 Python 3.8+
python scripts/validate_graph.py --help
```

## 关键改进（v1.1）

### 1. 可视化自动化

现在 `export_graph_html.py` 会生成两个完整可用的 HTML 文件：

- **graph_clean.html**：极简展示版
  - 深色渐变背景，标题居中
  - 点击节点右侧滑出详情面板
  - 左下角固定显示创作依据
  - 完全响应式布局，支持手机视图
  
- **graph_full.html**：完整分析版
  - 显示统计信息（节点/关系/根节点/孤立节点）
  - 高亮孤立节点和根节点功能
  - 显示/隐藏关系标签
  - 完整的质量指标报告

### 2. 连通性检测

`validate_graph.py` 新增 `--connectivity` 参数：

```bash
python scripts/validate_graph.py output/graph.json --connectivity

# 输出示例：
# 📊 Connectivity Analysis:
#   Total nodes: 36
#   Total relations: 38
#   Connected nodes: 36 (100.0%)
#   Root nodes: 1
#     C000
#   Isolated nodes: 0
#   Leaf nodes: 15
```

自动检测并警告：
- ⚠️ 多根节点（建议添加 Level 0 课程总根节点）
- ⚠️ 孤立节点（提供节点 ID 列表）
- ✅ 连通性指标（连通比例、根节点数、叶子节点数）

### 3. 标准验证层次

新增三级验证标准（详见 `references/standards-acquisition.md`）：

- **Level 1：标准发现**（必须完成）
- **Level 2：政策方向一致性**（最低要求）
- **Level 3：逐条对照验证**（理想状态）

输出时必须明确说明完成了哪个层次。

### 4. 根节点和孤立节点设计指导

新增完整的方法论（详见 `references/extraction-method.md`）：

- 如何添加 Level 0 课程总根节点
- 如何识别和消除孤立节点
- 提取阶段的检查清单
- 连通性验证步骤

## 项目结构

```
capability-graph-extractor/
├── SKILL.md                    # 主文档，定义职责分工和工作流程
├── README.md                   # 本文件，快速开始和决策树
├── references/                 # 方法论参考文档
│   ├── extraction-method.md    # 能力提取方法（新增根节点/孤立节点章节）
│   ├── standards-acquisition.md # 标准验证方法（新增三层次验证）
│   ├── incomplete-materials.md # 残缺资料降级策略
│   ├── four-graphs-relations.md # 四图谱关系定义
│   ├── evidence-review.md      # 证据分类和审核
│   └── course-paradigms.md     # 课程范式（职业课/公共课）
├── templates/                  # 输出结构模板
│   ├── graph.schema.json       # 图谱 JSON Schema
│   └── output-template.json    # 输出模板示例
├── scripts/                    # 确定性辅助脚本
│   ├── inventory_materials.py  # 材料清点
│   ├── assign_stable_ids.py    # 稳定 ID 分配
│   ├── validate_graph.py       # 图谱校验（新增连通性检测）
│   ├── export_graph_html.py    # HTML 可视化（升级为双版本）
│   └── export_formats.py       # CSV/Mermaid 导出
├── examples/                   # 示例和案例
│   └── README.md
└── evals/                      # 评估案例
    └── evals.json
```

## 常见问题

### 1. 脚本是必需的吗？

**不是**。如果材料少、节点少，完全可以手工完成。脚本只是可选的自动化工具。

### 2. 我必须使用 Python 吗？

**不是**。方法论（`references/` 目录）可以在任何环境中遵循。脚本是可选的便利工具。

### 3. 我可以修改脚本吗？

**可以**。所有脚本都是确定性的辅助工具，你可以根据需要修改或替换。

### 4. 如何处理多根节点？

参考 `references/extraction-method.md` 的"根节点设计"章节：
1. 识别所有模块级根节点
2. 添加 Level 0 课程总根节点（类型 `course_goal`）
3. 使用 `part_of` 关系连接总根到各模块根
4. 用 `validate_graph.py --connectivity` 验证

### 5. 如何消除孤立节点？

参考 `references/extraction-method.md` 的"孤立节点预防"章节：
1. 用 `validate_graph.py --connectivity` 检测孤立节点
2. 分析孤立原因（素质节点孤立、模块内未关联、跨模块未连接）
3. 补充合理的关系（前置、包容、平行）
4. 重新验证直到孤立节点数为 0

### 6. 标准验证必须完成 Level 3 吗？

**不是**。三个层次递进：
- Level 1（标准发现）是必须完成的
- Level 2（政策方向一致性）是最低要求
- Level 3（逐条对照）是理想状态，网络受限时可能无法完成

关键是**诚实披露**完成了哪个层次，不要模糊表述。

## 输出示例

标准交付包含：

```
output/
├── material_inventory.json      # 材料清单
├── capability_graph.json        # 完整图谱 JSON
├── capability_extraction_summary.md # 提取总结报告
├── graph_clean.html            # 极简展示版可视化（新）
├── graph_full.html             # 完整分析版可视化（新）
├── nodes.csv                   # 节点列表
├── relations.csv               # 关系列表
├── sources.csv                 # 来源列表
└── graph.mmd                   # Mermaid 概览图
```

## 许可

本 Skill 为方法论和辅助工具集合，可自由使用和修改。
