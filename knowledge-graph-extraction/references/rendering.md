# 图谱可视化渲染契约

`graph.json` → 交互式 HTML。两个渲染器零项目耦合，拷到任意项目的输出目录即可用。

---

## 一、渲染器

### `render_graph.py` — 2D（ECharts）

| 项 | 说明 |
|---|---|
| 产物 | 单文件 HTML，**默认内联 ECharts → 零 CDN 依赖，离线可开** |
| 布局 | Python 端 Fruchterman-Reingold 预计算坐标，ECharts `layout:'none'` 钉住 |
| 依赖 | numpy 为**软依赖**，缺失时退化为圆形布局（图仍可用，只是不好看） |
| 交互 | 点击节点聚焦高亮（双向邻边）、左侧学习路径导览下钻、搜索、tooltip |

```bash
python3 render_graph.py \
  --graph graph.json \               # 必需
  --output graph.html \              # 必需
  --title "大学生心理健康知识图谱" \   # 默认 "知识图谱"
  --path-json learning_path.json \   # 可选，左侧导览面板
  --echarts-js echarts.min.js \      # 可选，留空自动探测
  --cdn \                            # 可选，强制走 CDN 不内联
  --rel-colors '{"包含":"#60a5fa"}'   # 可选，关系配色覆盖/扩充
```

**ECharts 资源探测顺序**（命中即停；`--cdn` 直接跳过全部）：

1. `--echarts-js` 显式路径
2. 输出目录同级 `echarts.min.js`
3. 脚本目录同级 `echarts.min.js`
4. **skill 内置** `<skill>/assets/echarts.min.js` ← 通常命中这条，所以零配置可用
5. 全未命中 → 回退 `cdn.jsdelivr.net/npm/echarts@5.5.0`（此时产物需要外网）

### `render_graph_3d.py` — 3D（3d-force-graph）

| 项 | 说明 |
|---|---|
| 产物 | HTML，**运行时依赖 CDN，离线打不开** |
| 布局 | 不预计算，浏览器端力导向实时模拟 |
| 交互 | 同 2D（聚焦、导览、搜索、tooltip） |

```bash
python3 render_graph_3d.py \
  --graph graph.json --output graph_3d.html \
  --title "知识图谱 3D" --path-json learning_path.json
```

参数只有这 4 个——**没有** `--rel-colors` / `--cdn` / `--echarts-js`。运行时拉取：
`three@0.157.0`、`three-spritetext@1.8.2`、`3d-force-graph@1.73.4`（均来自 jsdelivr）。

---

## 二、输入数据契约

### `graph.json`（必需）

```jsonc
{
  "entities": [{
    "id": "29aa071c873048f18097cd804b665034",  // 确定性哈希
    "text": "认识心理健康",                     // 节点显示名
    "label": "心理概念",                        // 实体类型 → 决定节点配色
    "normalized_name": "认识心理健康",
    "description": "",                          // 可空，进详情面板
    "attributes": [                             // 注意：是数组，不是对象
      {"text": "fm-doc-id-e00d05d2", "label": "data-hash"},
      {"text": "7989331510455501413", "label": "unitId"},   // ← 关联学习路径
      {"text": "7989338958046233670", "label": "lessonId"}  // ← 关联学习路径
    ]
  }],
  "relations": [{
    "id": "d7997f96bcee101471b067ac42addbd3",
    "source_id": "...", "target_id": "...",
    "source_text": "认识心理健康", "target_text": "心理学",
    "text": "包含", "label": "包含",            // label 决定关系标签文字与配色
    "sources": ["markdown/8407877133787013120_....md"]
  }],
  "metadata": {
    "schema_version": 1, "kb_id": "college_mental_health",
    "merged_sources": 23, "entity_count": 291, "relation_count": 304
  }
}
```

**来源**：`raws.jsonl`（每行一次 LLM 抽取）经 `normalize_graph.py --merge` 去重合并生成。

### `learning_path.json`（可选）

**顶层是数组**，不是对象：

```json
[
  {
    "unitId": "7989331510455501413",
    "name": "项目一 · 健康心理 幸福人生",
    "lessons": [
      {"lessonId": "7989338958046233670", "title": "认识心理健康"},
      {"lessonId": "7989338958046233671", "title": "培养健康心理"}
    ]
  }
]
```

**联动机制**（关键）：面板拿 `unitId`/`lessonId` 去匹配**实体 `attributes` 里同名 label 的 `text`**。
点击单元 → 筛出所有带该 `unitId` 属性的节点 → 批量聚焦。
所以：**实体没带 `unitId`/`lessonId` 属性时，面板能显示但点了选不中任何节点**。
文件缺失时图谱正常渲染，只是没有左侧导览。

---

## 三、常用命令

**零配置渲染**（ECharts 自动从 skill assets 取）：

```bash
cd output/
python3 ~/.pi/agent/skills/knowledge-graph-extraction/scripts/render_graph.py \
  --graph graph.json --output graph.html --title "项目知识图谱"
```

**项目特化配色**（不改源码，配色写在命令里）：

```bash
python3 render_graph.py --graph graph.json --output graph.html \
  --rel-colors '{"定义":"#10b981","约束":"#ef4444","优化":"#8b5cf6"}'
```

未在 `DEFAULT_REL_COLORS` 也未在 `--rel-colors` 里的关系，前端回退 `#e2e8f0`。传入的 key 与默认表合并，同名覆盖。

**2D + 3D 一起出**（共用同一份 graph.json）：

```bash
python3 render_graph.py    --graph graph.json --output graph.html
python3 render_graph_3d.py --graph graph.json --output graph_3d.html
```

---

## 四、实现要点

### 关系标签：隐形节点 + `opacity:1`（易踩坑）

关系标签**不走 ECharts 原生 `edgeLabel`**（配置里 `edgeLabel.show:false`），而是在每条边中点建一个隐形节点（midNode）来承载文字。

隐形靠 `itemStyle:{opacity:0, color:'transparent'}`。但 **ECharts 的 label 默认继承所属 symbol 的 opacity**——`opacity:0` 会把标签一起变透明，即使 `label.show:true` 也完全看不见（而悬停走 tooltip/emphasis 路径不受影响，于是表现为"必须悬停才看得到关系名"）。

修复是在 label 上显式覆盖：

```js
label:{ show:true, opacity:1, ... }   // opacity:1 必须显式写
```

改这块务必同步改生成器，别只改产物 HTML。

### 节点配色

12 色调色板按**实体类型索引**循环取色，不是按类型名硬编码：

```js
palette = ['#60a5fa','#fbbf24','#34d399','#f87171','#22d3ee','#c084fc',
           '#fb923c','#4ade80','#f472b6','#38bdf8','#a3e635','#e879f9']
```

类型超过 12 种会循环复用颜色。未聚焦的节点统一置灰 `DIM = '#1e3a52'`。

### 3D 为何不预计算坐标

3D 力导向的观感强依赖视角、缩放、拖拽，预计算无意义；Python 端也缺成熟 3D 布局库。代价是首次加载要等几秒收敛，>500 节点可能卡。

---

## 五、FAQ

**改了 `graph.html` 重新生成就丢？**
必然丢——HTML 是产物。值得留的改动同步回 `render_graph.py` 再重跑。

**点击节点后关系标签不显示？**
查 label 配置里有没有显式 `opacity:1`，见上文"关系标签"。

**3D 图离线打不开？**
设计如此，运行时依赖 CDN。要离线得自行下载三个库并把脚本里的 CDN URL 改成本地路径。

**怎么改节点颜色？**
改脚本里的 `palette` 数组。目前没做成命令行参数——只有关系配色（`--rel-colors`）参数化了。

**能导出图片吗？**
没配 ECharts `toolbox`，所以没有内置导出按钮。2D 是 canvas，浏览器右键"图片另存为"可用；3D 用截图工具。

---

## 六、设计原则

1. **脚本零项目耦合** — 参数通用化，拷到任意项目直接跑，不改代码
2. **配色可覆盖** — 内置合理默认，项目特化经命令行注入
3. **2D 离线优先** — 内联 ECharts 是默认行为，不是可选项
4. **产物不可手改** — 一切改动回到生成器
