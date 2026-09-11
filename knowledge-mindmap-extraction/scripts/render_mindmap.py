#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_mindmap.py — 将 mindmap.json 渲染为单文件交互式思维导图 HTML（ECharts tree）。

与 render_graph.py 保持一致的 CLI 约定与资源内联策略：
  - echarts.min.js 自动探测（输出目录 → 脚本目录 → skill assets/），亦可 --echarts-js 指定
  - 默认零 CDN：资源内联进 HTML，离线可直接打开
  - --cdn 可显式退回 CDN 模式

数据契约（实测自 ECharts 5.5.0）：
  - tree series 数据字段为 name/children（mindmap.json 用 content，此处转换）
  - series.data 必须是数组：[root]
  - 逐节点 itemStyle / lineStyle 均生效，边继承子节点的 lineStyle → 支持分支配色
  - initialTreeDepth 恒传 -1：该项会在每次 notMerge 重渲染时重新套用，
    值 >= 0 会压过逐节点 collapsed:false（实测），故折叠状态一律由逐节点
    collapsed 接管（--initial-depth 喂给 setCollapsed，不传给 ECharts）
  - expandAndCollapse 默认开启
  - roam 默认关闭，需显式开启才能拖拽缩放
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# 与 graph.html 一致的深色主题配色
DEFAULT_PALETTE = [
    "#60a5fa", "#fbbf24", "#34d399", "#f87171",
    "#22d3ee", "#c084fc", "#fb923c", "#4ade80",
    "#f472b6", "#38bdf8", "#a3e635", "#e879f9",
]
ROOT_COLOR = "#93c5fd"
CDN_ECHARTS = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"


# --------------------------------------------------------------------------- #
# 数据转换
# --------------------------------------------------------------------------- #

def convert(node, depth=0, branch=-1):
    """mindmap.json {content, children} → ECharts tree {name, children, c, d}

    c = 分支配色索引（继承自一级祖先），d = 层深。
    具体样式在浏览器端按 c/d 推导，保持 payload 紧凑。
    """
    name = (node.get("content") or "").strip()
    out = {"name": name, "c": branch, "d": depth}

    kids = node.get("children") or []
    if kids:
        out["children"] = [
            convert(k, depth + 1, i if depth == 0 else branch)
            for i, k in enumerate(kids)
        ]
    return out


def tree_stats(node):
    """返回 (节点总数, 最大层深)。"""
    total, deepest = 1, node.get("d", 0)
    for k in node.get("children") or []:
        t, d = tree_stats(k)
        total += t
        deepest = max(deepest, d)
    return total, deepest


# --------------------------------------------------------------------------- #
# 资源探测（镜像 render_graph.py）
# --------------------------------------------------------------------------- #

def locate_echarts(explicit, out_dir):
    if explicit:
        if not os.path.isfile(explicit):
            sys.exit(f"[render_mindmap] --echarts-js 指定的文件不存在: {explicit}")
        return explicit

    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(out_dir, "echarts.min.js"),
        os.path.join(here, "echarts.min.js"),
        # skill 自身 assets/（位于 skill 根，即 scripts/ 的上一级）—— 与 render_graph.py 一致
        os.path.join(os.path.dirname(here), "assets", "echarts.min.js"),
        os.path.join(here, "assets", "echarts.min.js"),
        # 最后兵：复用 graph skill 已 vendored 的同一份资源
        os.path.expanduser(
            "~/.pi/agent/skills/knowledge-graph-extraction/assets/echarts.min.js"
        ),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


# --------------------------------------------------------------------------- #
# HTML 模板
# --------------------------------------------------------------------------- #

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:#0b1020;color:#e5e7eb;
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",
    "Microsoft YaHei","Noto Sans CJK SC",sans-serif;overflow:hidden}
  #app{display:flex;flex-direction:column;height:100%}

  header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
    padding:12px 18px;background:linear-gradient(180deg,#111a30 0%,#0d1426 100%);
    border-bottom:1px solid #1f2937;flex:0 0 auto}
  h1{margin:0;font-size:16px;font-weight:600;letter-spacing:.3px;color:#f1f5f9}
  .stats{font-size:12px;color:#64748b;white-space:nowrap}
  .stats b{color:#93c5fd;font-weight:600}
  .spacer{flex:1 1 auto}

  .tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  button{background:#16203a;color:#cbd5e1;border:1px solid #27334d;
    border-radius:7px;padding:6px 12px;font-size:12.5px;cursor:pointer;
    transition:background .15s,border-color .15s,color .15s}
  button:hover{background:#1e2b48;border-color:#3b4c6e;color:#f1f5f9}
  button:active{background:#243357}
  button.on{background:#1d4ed8;border-color:#3b82f6;color:#fff}

  input[type=search]{background:#0f1729;border:1px solid #27334d;border-radius:7px;
    padding:6px 11px;font-size:12.5px;color:#e5e7eb;width:170px;outline:none;
    transition:border-color .15s}
  input[type=search]:focus{border-color:#3b82f6}
  input[type=search]::placeholder{color:#475569}

  #chart{flex:1 1 auto;width:100%;min-height:0}

  #hint{position:absolute;left:18px;bottom:14px;font-size:11.5px;color:#475569;
    pointer-events:none;letter-spacing:.2px}
  #toast{position:absolute;left:50%;top:70px;transform:translateX(-50%);
    background:#1e293b;border:1px solid #334155;color:#e2e8f0;font-size:12.5px;
    padding:7px 14px;border-radius:8px;opacity:0;transition:opacity .2s;
    pointer-events:none}
  #toast.show{opacity:1}
</style>
</head>
<body>
<div id="app">
  <header>
    <h1>__TITLE__</h1>
    <span class="stats"><b>__NODE_COUNT__</b> 个节点 · <b>__DEPTH__</b> 层</span>
    <span class="spacer"></span>
    <div class="tools">
      <input type="search" id="q" placeholder="搜索节点…" autocomplete="off">
      <button id="expand">展开全部</button>
      <button id="collapse">折叠全部</button>
      <button id="fit">适应屏幕</button>
      <button id="layout">切换布局</button>
    </div>
  </header>
  <div id="chart"></div>
</div>
<div id="hint">点击节点展开 / 折叠 · 拖拽平移 · 滚轮缩放</div>
<div id="toast"></div>

__ECHARTS_TAG__
<script>
(function(){
  "use strict";

  var RAW      = __DATA__;
  var PALETTE  = __PALETTE__;
  var ROOT_COL = "__ROOT_COLOR__";
  var INIT_D   = __INITIAL_DEPTH__;
  var layout   = "__LAYOUT__";

  // ---- 按 c(分支索引) / d(层深) 推导逐节点样式 -------------------------- //
  function colorOf(n){
    return n.d === 0 ? ROOT_COL : PALETTE[((n.c % PALETTE.length) + PALETTE.length) % PALETTE.length];
  }
  // 层深越大字号越小、越淡，形成视觉层级
  var FONT = [19, 14, 12.5, 11.5, 11, 10.5];
  var SYM  = [15, 11, 8, 6.5, 5.5, 5];

  function decorate(n){
    var col = colorOf(n);
    var d   = Math.min(n.d, FONT.length - 1);
    var leaf = !(n.children && n.children.length);

    n.symbol     = (n.d === 0 || !leaf) ? "circle" : "emptyCircle";
    n.symbolSize = SYM[d];
    n.itemStyle  = {
      color: col,
      borderColor: n.d === 0 ? "#e0f2fe" : col,
      borderWidth: n.d === 0 ? 2 : 1,
      shadowBlur: n.d <= 1 ? 12 : 0,
      shadowColor: n.d <= 1 ? col : "transparent"
    };
    n.lineStyle  = { color: col, width: Math.max(3.2 - n.d * 0.55, 1) , opacity: 0.55 };
    n.label      = {
      fontSize: FONT[d],
      fontWeight: n.d === 0 ? 700 : (n.d === 1 ? 600 : 400),
      color: n.d === 0 ? "#f8fafc" : (n.d === 1 ? col : "#cbd5e1")
    };
    (n.children || []).forEach(decorate);
    return n;
  }

  // 深拷贝，保证展开/折叠重建时不污染原数据
  function clone(o){ return JSON.parse(JSON.stringify(o)); }

  function setCollapsed(n, flag, depth){
    // depth 为 null 时对全树生效；否则只折叠 d >= depth 的节点
    var has = n.children && n.children.length;
    if (has) n.collapsed = (depth === null) ? flag : (n.d >= depth);
    (n.children || []).forEach(function(k){ setCollapsed(k, flag, depth); });
  }

  var DATA = decorate(clone(RAW));
  // 初始折叠状态直接写进逐节点 collapsed，使其成为唯一事实来源。
  // 不依赖 initialTreeDepth：该项会在每次 notMerge 重渲染时重新套用，
  // 覆盖掉展开全部/折叠全部/搜索定位所设置的 collapsed（实测结论）。
  setCollapsed(DATA, false, INIT_D);

  var chart = echarts.init(document.getElementById("chart"), null, { renderer: "canvas" });

  function baseSeries(data){
    var radial = layout === "radial";
    return {
      type: "tree",
      data: [data],
      layout: radial ? "radial" : "orthogonal",
      orient: "LR",
      roam: true,
      expandAndCollapse: true,
      animationDuration: 420,
      animationDurationUpdate: 420,
      initialTreeDepth: -1,
      edgeShape: "curve",
      nodeScaleRatio: 0.5,
      top: radial ? "6%" : "3%",
      bottom: radial ? "6%" : "3%",
      left: radial ? "10%" : "9%",
      right: radial ? "10%" : "22%",
      symbolSize: 8,
      label: {
        position: radial ? "right" : "left",
        verticalAlign: "middle",
        align: radial ? "left" : "right",
        distance: 7,
        rotate: radial ? undefined : 0,
        overflow: "truncate",
        width: 210
      },
      leaves: {
        label: { position: "right", verticalAlign: "middle", align: "left" }
      },
      emphasis: {
        focus: "descendant",
        itemStyle: { shadowBlur: 18 },
        label: { color: "#ffffff", fontWeight: 600 }
      },
      blur: { itemStyle: { opacity: 0.25 }, lineStyle: { opacity: 0.1 }, label: { opacity: 0.25 } }
    };
  }

  function render(data){
    chart.setOption({
      backgroundColor: "#0b1020",
      tooltip: {
        trigger: "item",
        triggerOn: "mousemove",
        backgroundColor: "rgba(15,23,41,.96)",
        borderColor: "#334155",
        textStyle: { color: "#e2e8f0", fontSize: 12.5 },
        extraCssText: "max-width:340px;white-space:normal;line-height:1.6;border-radius:8px",
        formatter: function(p){
          var n = p.data || {};
          var kids = (n.children || []).length;
          return "<b style='color:" + colorOf(n) + "'>" + p.name + "</b>"
               + "<br><span style='color:#64748b'>第 " + n.d + " 层"
               + (kids ? " · " + kids + " 个子节点" : " · 叶节点") + "</span>";
        }
      },
      series: [baseSeries(data)]
    }, { notMerge: true });
  }

  render(DATA);

  // ---- 工具栏 ----------------------------------------------------------- //
  function toast(msg){
    var t = document.getElementById("toast");
    t.textContent = msg; t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(function(){ t.classList.remove("show"); }, 1600);
  }

  function rebuild(flag, depth){
    var d = decorate(clone(RAW));
    setCollapsed(d, flag, depth);
    DATA = d;
    render(DATA);
  }

  document.getElementById("expand").onclick = function(){
    rebuild(false, null); toast("已展开全部节点");
  };
  document.getElementById("collapse").onclick = function(){
    rebuild(true, 1); toast("已折叠至一级分支");
  };
  document.getElementById("fit").onclick = function(){
    render(DATA); toast("已重置视图");
  };
  document.getElementById("layout").onclick = function(){
    layout = (layout === "radial") ? "orthogonal" : "radial";
    this.classList.toggle("on", layout === "radial");
    render(DATA);
    toast(layout === "radial" ? "径向布局" : "正交布局");
  };

  // ---- 搜索：命中则展开其祖先链并高亮 ----------------------------------- //
  function search(kw){
    kw = (kw || "").trim();
    if (!kw){ rebuild(true, INIT_D); return; }

    var d = decorate(clone(RAW));
    var hits = 0;

    // 后序遍历：子树若命中则保持展开
    (function walk(n, chain){
      var mine = n.name.indexOf(kw) >= 0;
      if (mine) hits++;
      var anyKid = false;
      (n.children || []).forEach(function(k){ if (walk(k, chain.concat(n))) anyKid = true; });
      var keep = mine || anyKid;
      if (n.children && n.children.length) n.collapsed = !keep;
      if (mine){
        n.itemStyle.color = "#ffffff";
        n.itemStyle.borderColor = "#fbbf24";
        n.itemStyle.borderWidth = 2.5;
        n.itemStyle.shadowBlur = 20;
        n.itemStyle.shadowColor = "#fbbf24";
        n.symbolSize = Math.max(n.symbolSize, 10);
        n.label.color = "#fde68a";
        n.label.fontWeight = 700;
      }
      return keep;
    })(d, []);

    DATA = d;
    render(DATA);
    toast(hits ? "命中 " + hits + " 个节点" : "未找到「" + kw + "」");
  }

  var qEl = document.getElementById("q"), qT;
  qEl.addEventListener("input", function(){
    clearTimeout(qT);
    var v = this.value;
    qT = setTimeout(function(){ search(v); }, 280);
  });
  qEl.addEventListener("keydown", function(e){
    if (e.key === "Escape"){ this.value = ""; search(""); this.blur(); }
  });

  window.addEventListener("resize", function(){ chart.resize(); });

  // 暴露给浏览器端验证
  window.mm = chart;
  window.mmData = function(){ return DATA; };
})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(
        description="将 mindmap.json 渲染为单文件交互式思维导图 HTML（ECharts tree）"
    )
    ap.add_argument("--mindmap", default="mindmap.json", help="输入 mindmap.json 路径")
    ap.add_argument("--output", default="mindmap.html", help="输出 HTML 路径")
    ap.add_argument("--title", default="知识导图", help="页面标题")
    ap.add_argument("--echarts-js", default=None,
                    help="echarts.min.js 路径（默认自动探测）")
    ap.add_argument("--cdn", action="store_true",
                    help="使用 CDN 加载 echarts（默认内联，零 CDN）")
    ap.add_argument("--layout", choices=["orthogonal", "radial"], default="orthogonal",
                    help="初始布局：orthogonal 正交（默认，中文标签可读性最佳）/ radial 径向")
    ap.add_argument("--initial-depth", type=int, default=2,
                    help="初始展开层深（对应 ECharts initialTreeDepth，默认 2）")
    ap.add_argument("--colors", default=None,
                    help="自定义分支配色，逗号分隔的 hex，如 '#60a5fa,#fbbf24'")
    args = ap.parse_args()

    if not os.path.isfile(args.mindmap):
        sys.exit(f"[render_mindmap] 输入文件不存在: {args.mindmap}")

    with open(args.mindmap, "r", encoding="utf-8") as f:
        raw = json.load(f)

    data = convert(raw)
    count, depth = tree_stats(data)

    palette = DEFAULT_PALETTE
    if args.colors:
        palette = [c.strip() for c in args.colors.split(",") if c.strip()]
        if not palette:
            sys.exit("[render_mindmap] --colors 未解析出任何颜色")

    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."

    # ---- echarts 资源 ---- #
    if args.cdn:
        echarts_tag = f'<script src="{CDN_ECHARTS}"></script>'
        src_note = f"CDN ({CDN_ECHARTS})"
    else:
        path = locate_echarts(args.echarts_js, out_dir)
        if not path:
            sys.exit(
                "[render_mindmap] 未找到 echarts.min.js。\n"
                "  请用 --echarts-js 指定路径，或加 --cdn 走 CDN 模式。"
            )
        with open(path, "r", encoding="utf-8") as f:
            js = f.read()
        # 内联时必须转义闭合标签，避免提前终止 <script>
        js = js.replace("</script>", "<\\/script>")
        echarts_tag = f"<script>{js}</script>"
        src_note = f"内联 {path} ({len(js)/1024:.0f} KB)"

    html = (
        TEMPLATE
        .replace("__TITLE__", args.title)
        .replace("__NODE_COUNT__", str(count))
        .replace("__DEPTH__", str(depth + 1))
        .replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        .replace("__PALETTE__", json.dumps(palette))
        .replace("__ROOT_COLOR__", ROOT_COLOR)
        .replace("__INITIAL_DEPTH__", str(args.initial_depth))
        .replace("__LAYOUT__", args.layout)
        .replace("__ECHARTS_TAG__", echarts_tag)
    )

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(args.output) / 1024
    print(f"[render_mindmap] 已生成 {args.output} ({size:.0f} KB)")
    print(f"  节点 {count} 个 · {depth + 1} 层 · 布局 {args.layout} · 初始展开 {args.initial_depth} 层")
    print(f"  echarts: {src_note}")


if __name__ == "__main__":
    main()
