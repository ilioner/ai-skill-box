# Mindmap 渲染契约

本文档记录 `scripts/render_mindmap.py` 的实测契约（ECharts 5.5.0 tree series）与已知陷阱。

---

## 一、CLI 约定

```bash
python scripts/render_mindmap.py \
  --mindmap <path/to/mindmap.json> \
  --output <path/to/output.html> \
  [--title "页面标题"] \
  [--layout orthogonal|radial] \
  [--initial-depth N] \
  [--echarts-js <path/to/echarts.min.js>] \
  [--colors '#hex1,#hex2'] \
  [--cdn]
```

| 参数 | 说明 |
|---|---|
| `--mindmap` | 输入 JSON（`{content, children}` 树结构，顶层直接是根节点对象） |
| `--output` | 输出 HTML 路径 |
| `--title` | 页面标题（默认 "知识导图"） |
| `--layout` | `orthogonal`（默认，正交布局）或 `radial`（径向布局） |
| `--initial-depth` | 初始展开层数（默认 2）。**语义见下方警告**：`-1`/`0` = 全部折叠，全展开需传大于树深的值（如 99） |
| `--colors` | 自定义分支配色，逗号分隔 hex（如 `'#60a5fa,#fbbf24'`）；缺省用内置 12 色 palette |
| `--echarts-js` | 指定 echarts.min.js 路径（可选；不指定时自动探测：输出目录 → 脚本目录 → skill assets/ → graph skill assets/） |
| `--cdn` | 从 CDN 加载 echarts（不指定时默认内联本地资源，零 CDN） |

**资源探测顺序**（未传 `--echarts-js` 时）
1. `<output_dir>/echarts.min.js`
2. `<script_dir>/echarts.min.js`
3. `<script_dir>/assets/echarts.min.js`（skill 自身 assets/）
4. `~/.pi/agent/skills/knowledge-graph-extraction/assets/echarts.min.js`（复用 graph skill）

> ⚠️ **`--initial-depth` 的语义与 ECharts 原生 `initialTreeDepth` 相反，勿混淆。**
> 该参数并不传给 ECharts（那里恒为 `-1`，见第三节），而是喂给逐节点 `setCollapsed`，
> 判据为 `collapsed = (node.depth >= initial_depth)`。因所有节点 `depth >= 0`：
>
> | 传值 | 实际效果（node 实测） |
> |---|---|
> | `-1` | 全部折叠（根节点自身亦折叠） |
> | `0` | 全部折叠（同 `-1`，仅根节点可见） |
> | `1` | 展开 1 层 |
> | `2`（默认） | 展开 2 层 |
> | `99`（≥ 树最大深度） | 全展开 |
>
> ECharts 原生 `initialTreeDepth` 中 `-1` 表示全展开——此处**不成立**。

---

## 二、ECharts tree series 数据契约

实测自 ECharts 5.5.0（vendored 本地资源）。

### 2.1 输入数据结构

mindmap.json 原始结构：
```json
{
  "content": "根节点文本",
  "data-hash": "...",
  "children": [
    {"content": "子节点", "children": [...]}
  ]
}
```

ECharts tree series 要求字段名为 `name`（非 `content`），根节点须包成数组传入 `series.data`：
```javascript
{
  series: [{
    type: 'tree',
    data: [root],  // 必须是数组
    ...
  }]
}
```

`render_mindmap.py` 的 `convert()` 函数负责字段转换：`content` → `name`，并为每个节点标记 `c`（分支配色索引）与 `d`（层深）。

### 2.2 布局与交互配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `layout` | `'orthogonal'` | `'orthogonal'` / `'radial'`。**正交 LR 默认**（中文标签保持水平可读），径向布局标签会随角度旋转、竖向位置几乎不可读。 |
| `orient` | `'LR'` | 正交布局方向：`'LR'` 左→右，`'TB'` 上→下 |
| `expandAndCollapse` | `true` | 点击展开/折叠子树，默认已开启 |
| `roam` | `false` | **默认关闭**，需显式设为 `true` 或 `'scale'` / `'move'` 才能拖拽缩放 |
| `initialTreeDepth` | `2` | 初始展开层数（`-1` = 全展开，`>= 0` = 折叠 depth >= 该值的节点）|

### 2.3 样式定制

- **逐节点样式**：`itemStyle` / `lineStyle` / `label` 均生效。
- **边继承子节点颜色**：连线 `lineStyle.color` 继承子节点的 `itemStyle.color`，支持分支配色方案。
- **配色**：沿用 graph.html 深色主题（背景 `#0b1020` + 12 色 palette），视觉一致。

---

## 三、关键陷阱与修法

### 3.1 initialTreeDepth 重渲染陷阱 ⚠️

**现象**  
「展开全部」按钮调用 `chart.setOption({series: [{data: [expandedRoot]}]}, {notMerge: true})` 后，逐节点设置的 `collapsed: false` 被压过，300 个节点仍保持折叠态。

**根因**  
`initialTreeDepth: N`（N >= 0）在每次 `notMerge` 重渲染时都会被重新套用，压过逐节点 `collapsed` 标志。

**修法**  
- `initialTreeDepth` 只能用于首次渲染；重渲染时必须传 `-1` 并交由逐节点 `collapsed` 接管。
- **唯一事实来源**：让逐节点 `collapsed` 成为折叠状态的唯一控制点，首渲染也不依赖 `initialTreeDepth`。

**实测对照**（398 节点真实数据）
| 配置 | 结果 | 通过 |
|---|---|---|
| `initialTreeDepth: -1` + 逐节点 `collapsed: false` | 398 展开 / 0 折叠 | ✓ |
| `initialTreeDepth: 2` + 逐节点 `collapsed: false` | 98 展开 / 300 折叠 | ✗（initialTreeDepth 压过逐节点） |
| `initialTreeDepth: -1` + 逐节点 `collapsed` | 10 展开 / 88 折叠 | ✓（逐节点标志被尊重） |

**代码修复**（已落在 `render_mindmap.py` 内联脚本，以下为逐字摘录）
```javascript
var INIT_D = __INITIAL_DEPTH__;          // CLI 参数 --initial-depth（默认 2）

// 逐节点写入 collapsed：depth 为 null 时对全树生效；否则只折叠 d >= depth 的节点
function setCollapsed(n, flag, depth){
  var has = n.children && n.children.length;
  if (has) n.collapsed = (depth === null) ? flag : (n.d >= depth);
  (n.children || []).forEach(function(k){ setCollapsed(k, flag, depth); });
}
setCollapsed(DATA, false, INIT_D);       // 首渲染：展开前 INIT_D 层

// baseSeries：恒传 -1，把折叠状态完全交给逐节点 collapsed
initialTreeDepth: -1,

// 四条交互路径共用同一个 rebuild —— 唯一事实来源
function rebuild(flag, depth){
  var d = decorate(clone(RAW));
  setCollapsed(d, flag, depth);
  DATA = d;
  render(DATA);
}
document.getElementById("expand").onclick   = function(){ rebuild(false, null); };  // 展开全部
document.getElementById("collapse").onclick = function(){ rebuild(true, 1); };      // 折叠至一级分支
// 搜索清空分支：rebuild(true, INIT_D) —— 恢复初始 INIT_D 层展开态
```

### 3.2 搜索清空重置语义 ⚠️

**设计**  
- 搜索关键词 → 精准展开匹配节点的全部祖先路径（仅该路径，其余保持折叠）。
- 清空搜索（Escape / 清空输入框）→ 恢复到初始 `INIT_D` 层展开态（**非**折叠到一级分支）。
- 「折叠全部」按钮 → 折叠到仅根节点展开。

**实测**（398 节点真实数据）
- 初始：10 展开 / 88 折叠（INIT_D=2）
- 折叠全部后搜索「心理学是什么？」（深度 4 叶节点）→ 精确展开 4 层祖先路径（94 折叠 / 4 展开）✓
- Escape 清空 → 恢复 10/88（初始态）✓

**代码**
```javascript
function clearSearch() {
  setCollapsed(DATA, false, INIT_D);  // 恢复初始 INIT_D，非硬编码 1
  chart.setOption({series: [{data: [DATA]}]}, {notMerge: true});
  searched.clear();
}
```

---

## 四、浏览器验证检查表

| 项 | 检查点 | 通过标准 |
|---|---|---|
| 基线 | 398 节点加载完成、初始 10 展开/88 折叠、0 JS 错误 | ✓ |
| 展开全部 | 点击按钮 → 0 折叠 / 98 展开（修复前卡在 300 折叠） | ✓ |
| 折叠全部 | 仅根节点展开 | ✓ |
| 布局切换 | 正交 ↔ 径向，展开状态完整保持 | ✓ |
| 搜索展开 | 搜索深层节点 → 精确展开祖先路径 | ✓ |
| 搜索清空 | Escape / 清空输入框 → 恢复初始 INIT_D 态（非 1 层） | ✓ |
| 零 CDN | 无 `src=` / `@import` / `fetch(` 加载时请求 | ✓ |
| 画布绘制 | 预设 viewport 打开，画布正常绘制、无空白 | ✓ |

**零 CDN 验证口径**  
确认无运行时加载请求（`src=` / `@import` / `fetch(`），而非仅凭 `https://` 字符串判定；包元数据字符串（如 zrender LICENSE）与品牌链接 `href`（仅点击跳转）不构成 CDN 依赖。

---

## 五、已知约束

### 5.1 DOM 结构

- **容器 id**：`#chart`（非 `#mindmap`）
- **搜索框 id**：`#q`
- **工具栏按钮**：展开全部 / 折叠全部 / 切换布局 / 适应屏幕

### 5.2 viewport 预设

页面加载后再改 viewport 会导致画布空白（数据与交互仍在、仅不绘制）；浏览器验证时须在打开页面时预设 viewport，或事后调用 `chart.resize()`。

### 5.3 真实点击原则

ECharts `dispatchAction` 不支持派发 `click` 事件；浏览器验证中触发节点展开须用真实鼠标点击节点像素坐标（与 graph.html 一致）。

---

## 六、与 graph skill 对齐的惯例

| 项 | graph skill | mindmap skill |
|---|---|---|
| vendored 资源 | `assets/echarts.min.js` | `assets/echarts.min.js`（同一份） |
| 渲染脚本 | `scripts/render_graph.py` | `scripts/render_mindmap.py` |
| 契约文档 | `references/rendering.md` | `references/rendering.md`（本文档） |
| 零 CDN 默认 | ✓ | ✓ |
| 深色主题配色 | `#0b1020` + 12 色 palette | 同 graph.html |

---

**最后更新**：2025-01-15  
**验证环境**：ECharts 5.5.0 · 398 节点 / 6 层真实数据 · macOS Chrome
