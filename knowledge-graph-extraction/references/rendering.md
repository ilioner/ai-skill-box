# 图谱可视化渲染契约

`graph.json` → 交互式 HTML。两个渲染器零项目耦合，拷到任意项目的输出目录即可用。

**默认数据与页面分离**：两个页面均通过 `fetch` 读取外部图谱与学习路径 JSON，HTML 中不保存实体、关系、详情或导览的数据副本。更新 JSON 后刷新即可，页面不自动轮询。仅用户明确要求单文件内嵌数据时使用 `--embed-data`；`--runtime-load` 保留为默认模式的显式选项，两者互斥。

### 数据地址与访问方式

- `--graph` 和 `--path-json` 是生成时的本地文件路径；生成器按它们与 `--output` 的相对位置生成 URL，不硬编码 `graph.json`，不把本机绝对路径写入页面。支持中文、空格和特殊字符文件名。
- 未指定 `--path-json` 时，运行时加载图谱同目录的 `learning_path.json`；仅该默认文件返回 404 时允许空导览。显式指定的学习路径缺失、JSON 解析失败或图谱加载失败均显示错误，不用内嵌旧数据兜底。
- 用 HTTP/HTTPS 打开，不能直接双击 `file://`。将 HTML 与 JSON 按原相对目录一起部署到服务中；在二者的公共目录运行 `python3 -m http.server 8000 --bind 127.0.0.1`，访问 `http://127.0.0.1:8000/` 下的页面。
- 2D 默认内联的是 **ECharts 运行库**，不是课程图谱数据；可在无外网环境中通过本地 HTTP 服务使用。3D 还需访问 CDN。

---

## 一、渲染器

### `render_graph.py` — 2D（ECharts）

| 项 | 说明 |
|---|---|
| 产物 | HTML 与 JSON 分离，默认内联 ECharts 运行库，无 CDN 依赖，须通过 HTTP 打开 |
| 布局 | 默认浏览器读取图谱后计算 Fruchterman-Reingold 坐标，ECharts `layout:'none'` 钉住 |
| 依赖 | 默认模式不需要 NumPy；仅 `--embed-data` 的 Python 预计算使用 NumPy 软依赖，缺失时退化为圆形布局 |
| 交互 | 点击节点聚焦高亮（双向邻边）、左侧学习路径导览下钻、搜索、tooltip |

```bash
python3 render_graph.py \
  --graph graph.json \               # 必需
  --output graph.html \              # 必需
  --title "大学生心理健康知识图谱" \   # 默认 "知识图谱"
  --path-json learning_path.json \   # 可省：缺省自动探测 graph 同目录 learning_path.json
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
| 产物 | HTML 与 JSON 分离，须通过 HTTP 打开，运行库依赖 CDN |
| 布局 | 不预计算，浏览器端力导向实时模拟 |
| 交互 | 同 2D（聚焦、导览、搜索、tooltip） |

```bash
python3 render_graph_3d.py \
  --graph graph.json --output graph_3d.html \
  --title "知识图谱 3D" --path-json learning_path.json   # --path-json 同样可省，自动探测同目录
```

除上述 4 个参数外，还支持互斥的 `--runtime-load`（默认）和 `--embed-data`；**没有** `--rel-colors` / `--cdn` / `--echarts-js`。运行时拉取：
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

**来源**：`raws.jsonl`（每行一次 LLM 抽取）经 `normalize_graph.py --merge` 去重合并生成基础图谱；教材/课程再用 `enrich_graph_codes.py` 补齐最终 `graph.json`，完整扩展字段契约见 `references/entity-codes.md`。实体额外包含 `code/book_code/chapter_no/section_no/knowledge_no/location_codes`，元数据额外包含 `entity_code_scheme`。2D/3D 渲染器已读取 `code` 在详情面板展示；归属下钻仍使用 `attributes` 中的 `unitId/lessonId`，不能用 `location_codes` 替换原属性。

### `learning_path.json`（默认生成）

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

**来源（默认生成，纯确定性）**：`build_learning_path.py` 从源文件产出 —— 用 `--source-dir <源目录>` 递归扫 md，或 `--raws raws.jsonl` 读每条记录的 `source` 字段；每个 `unitId` 归一组、`lessonId` 去重，`unitId`/`lessonId` 取 frontmatter（或 `<textbookId>_<unitId>_<lessonId>.md` 文件名），标题取首个 H1（单元）/首个 H2（课时）。`render_graph.py` / `render_graph_3d.py` **缺省自动探测 graph 同目录的 `learning_path.json`**，因此导览面板默认就填充，不必手传 `--path-json`。

**联动机制**（关键）：面板拿 `unitId`/`lessonId` 去匹配**实体 `attributes` 里同名 label 的 `text`**。
点击单元 → 筛出所有带该 `unitId` 属性的节点 → 批量聚焦。
所以：**实体必须带 `unitId`/`lessonId` 属**（normalize 默认按 source 回填），否则面板能显示但点了选不中任何节点。
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

**2D 直接双击提示无法加载 JSON？**
默认分离模式受浏览器 `file://` 限制，必须通过 HTTP 服务读取外部 JSON。启动本地服务即可，不要为绕过该限制擅自改回内嵌模式。

**怎么改节点颜色？**
改脚本里的 `palette` 数组。目前没做成命令行参数——只有关系配色（`--rel-colors`）参数化了。

**能导出图片吗？**
没配 ECharts `toolbox`，所以没有内置导出按钮。2D 是 canvas，浏览器右键"图片另存为"可用；3D 用截图工具。

---

## 六、设计原则

1. **脚本零项目耦合** — 参数通用化，拷到任意项目直接跑，不改代码
2. **配色可覆盖** — 内置合理默认，项目特化经命令行注入
3. **数据分离优先** — 默认外部加载 JSON；2D 默认内联 ECharts 运行库以减少外网依赖
4. **产物不可手改** — 一切改动回到生成器


## 移动端适配契约（2D / 3D 共用）

已将项目最终样本 `output2/graph.html`、`output2/graph_3d.html` 的移动适配层同步到两个渲染器模板。仅迁移响应式 CSS、移动交互脚本和 viewport，不替换图谱数据、桌面主题、渲染逻辑；样本不是运行时依赖。

- 断点：`max-width:1000px`；桌面保留原布局。
- 手机工具条单行：左侧「路径」48px，中间自适应搜索（重置为 ↺），右侧「详情」48px；触控高度至少44px。
- 搜索聚焦展开为全宽并隐藏两侧入口，失焦恢复；Enter 搜索、Escape 退出输入。
- 路径为左抽屉，详情为右抽屉，互斥展开；点击节点更新详情后自动打开右抽屉。支持入口切换、×、遮罩、Escape关闭。
- 图例常驻底部横向滚动，考虑安全区；抽屉不覆盖搜索和图例。
- 保留主题、颜色、字体、数据以及原搜索/重置逻辑。2D仍默认内联ECharts；3D仍依赖CDN，移动适配没有添加外部依赖。
- 回归检查：320/390/844/1440px下无水平溢出；验证搜索展开/恢复、抽屉互斥/关闭、详情自动打开、桌面恢复。使用临时输出验证，勿覆盖用户定稿HTML。
