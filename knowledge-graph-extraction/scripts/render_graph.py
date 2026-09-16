#!/usr/bin/env python3
"""把 graph.json 渲染成自包含交互式 HTML（ECharts 2D 力导向图）。

与 3D 版（graph_3d.html）组件/面板对齐——生长动画、学习路径导览、卡片式详情面板、
图例、聚焦淡化——差异仅在于图形以 2D ECharts 呈现（3D 版为 three.js 球体）。

布局：Python 端预计算 Fruchterman-Reingold 力导向坐标并嵌入节点，ECharts 用
layout:'none' 钉住位置；生长时逐层揭示节点（位置固定，仅进入动画），不产生力扰动。

用法：
  python3 render_graph.py --graph graph.json --output graph.html \
      --title "标题" --path-json learning_path.json
"""
from __future__ import annotations
import argparse, json, html
from pathlib import Path


def fr_layout(node_ids, edges, iters=300, seed=42):
    """Fruchterman-Reingold 2D 力导向布局，返回 {id:(x,y)}，坐标归一化到 ±900。

    针对树状层级图的两个关键处理：
    - 斥力截断：距离 > 2.5k 的节点对之间不再互斥，避免稀疏图被掉成“外环密集、
      中间空洞”的大圆环；
    - 引力限幅：单边拉力封顶，长边温和收缩不振荡。"""
    n = len(node_ids)
    if n == 0:
        return {}
    try:
        import numpy as np
    except ImportError:
        # 纯标准库回退：不能退化成圆形，否则无论图结构如何都会得到圆环。
        # 使用确定性的 Fruchterman-Reingold 迭代，保证没有 numpy 时仍保留拓扑聚类。
        import math, random
        rng = random.Random(seed)
        L = 1400.0
        pos = {nid: [rng.uniform(-L / 2, L / 2), rng.uniform(-L / 2, L / 2)]
               for nid in node_ids}
        k = 2.2 * math.sqrt((L * L) / n)
        valid_edges = [(a, b) for a, b in edges if a in pos and b in pos]
        t = L * 0.1
        dt = t / (iters + 1)
        for _ in range(iters):
            disp = {nid: [0.0, 0.0] for nid in node_ids}
            for i, a in enumerate(node_ids):
                ax, ay = pos[a]
                for b in node_ids[i + 1:]:
                    dx, dy = ax - pos[b][0], ay - pos[b][1]
                    dist = math.hypot(dx, dy) + 1e-9
                    if dist <= 2.5 * k:
                        f = (k * k) / dist
                        vx, vy = dx / dist * f, dy / dist * f
                        disp[a][0] += vx; disp[a][1] += vy
                        disp[b][0] -= vx; disp[b][1] -= vy
            for a, b in valid_edges:
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                dist = math.hypot(dx, dy) + 1e-9
                f = min((dist * dist) / k, 6.0 * k)
                vx, vy = dx / dist * f, dy / dist * f
                disp[a][0] -= vx; disp[a][1] -= vy
                disp[b][0] += vx; disp[b][1] += vy
            for nid in node_ids:
                x, y = pos[nid]
                disp[nid][0] -= x * 0.012; disp[nid][1] -= y * 0.012
                d = math.hypot(*disp[nid]) + 1e-9
                step = min(d, t)
                pos[nid][0] += disp[nid][0] / d * step
                pos[nid][1] += disp[nid][1] / d * step
            t -= dt
        cx = sum(p[0] for p in pos.values()) / n
        cy = sum(p[1] for p in pos.values()) / n
        for p in pos.values(): p[0] -= cx; p[1] -= cy
        scale = max(max(abs(p[0]) for p in pos.values()), max(abs(p[1]) for p in pos.values()), 1e-9)
        return {nid: (round(p[0] / scale * 900.0, 1), round(p[1] / scale * 900.0, 1))
                for nid, p in pos.items()}
    import numpy as np
    idx = {nid: i for i, nid in enumerate(node_ids)}
    rng = np.random.RandomState(seed)
    L = 1400.0
    pos = rng.rand(n, 2) * L - L / 2
    k = 2.2 * (L * L / n) ** 0.5                 # 理想边长
    rcut = 2.5 * k                               # 斥力截断半径：远簇不互斥，固住大环
    ea = np.array([[idx[a], idx[b]] for a, b in edges if a in idx and b in idx], dtype=int) \
        if edges else np.zeros((0, 2), int)
    t = L * 0.1; dt = t / (iters + 1)
    for _ in range(iters):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt((delta ** 2).sum(-1)) + 1e-9
        force = (k * k) / dist                    # 斥力 k²/d
        force[dist > rcut] = 0.0                  # 截断远距离斥力，防止掉成空心大环
        np.fill_diagonal(force, 0)
        disp = (delta / dist[..., None] * force[..., None]).sum(1)
        if len(ea):
            d = pos[ea[:, 0]] - pos[ea[:, 1]]
            dd = np.sqrt((d ** 2).sum(-1)) + 1e-9
            fmag = np.minimum((dd * dd) / k, 6.0 * k)     # 引力限幅：长边温和收缩
            fv = d / dd[:, None] * fmag[:, None]
            np.add.at(disp, ea[:, 0], -fv)
            np.add.at(disp, ea[:, 1], fv)
        disp += -pos * 0.012                      # 适度向心引力，防止过度扩散
        length = np.sqrt((disp ** 2).sum(-1)) + 1e-9
        pos += (disp / length[:, None]) * np.minimum(length, t)[:, None]
        t -= dt
    pos -= pos.mean(0)
    # 裁剪离散叶子，再整体归一化到 ±900 范围（便于ECharts渲染与交互）
    r = np.sqrt((pos ** 2).sum(-1)); rc = np.percentile(r, 94)
    pos[r > rc] = pos[r > rc] / r[r > rc, None] * rc
    rmax = np.max(np.abs(pos)) + 1e-9
    pos = pos / rmax * 900.0
    return {node_ids[i]: (round(float(pos[i, 0]), 1), round(float(pos[i, 1]), 1)) for i in range(n)}


def build_payload(graph: dict) -> dict:
    entities = graph.get("entities") or []
    relations = graph.get("relations") or []
    deg: dict[str, int] = {}
    for r in relations:
        deg[r["source_id"]] = deg.get(r["source_id"], 0) + 1
        deg[r["target_id"]] = deg.get(r["target_id"], 0) + 1
    labels = []
    for e in entities:
        if e["label"] not in labels:
            labels.append(e["label"])
    cat_index = {lab: i for i, lab in enumerate(labels)}
    id_ok = {e["id"] for e in entities}

    edges = [(r["source_id"], r["target_id"]) for r in relations
             if r["source_id"] in id_ok and r["target_id"] in id_ok]
    pos = fr_layout([e["id"] for e in entities], edges)
    # 层级：从度最高的根节点 BFS（无向全边），depth = 距根的图距离
    from collections import defaultdict, deque
    adj = defaultdict(list)
    for a, b in edges:
        adj[a].append(b); adj[b].append(a)
    root_id = max(entities, key=lambda e: deg.get(e["id"], 0))["id"]
    depth = {root_id: 0}
    dq = deque([root_id])
    while dq:
        x = dq.popleft()
        for y in adj[x]:
            if y not in depth:
                depth[y] = depth[x] + 1
                dq.append(y)
    for e in entities:                      # 兜底：不可达（理论不发生）
        depth.setdefault(e["id"], 4)

    def attrs_of(e, lab):
        return [a["text"] for a in (e.get("attributes") or []) if a.get("label") == lab]

    nodes = []
    for e in entities:
        d = deg.get(e["id"], 0)
        x, y = pos.get(e["id"], (0.0, 0.0))
        nodes.append({
            "id": e["id"], "name": e["text"], "category": cat_index[e["label"]],
            "label_text": e["label"], "val": max(1, d), "x": x, "y": y,
            "depth": depth.get(e["id"], 4),
            "desc": e.get("description") or "",
            "hashes": attrs_of(e, "data-hash"),
            "units": attrs_of(e, "unitId"), "lessons": attrs_of(e, "lessonId"),
            "textbook": (attrs_of(e, "textbookId") or [""])[0],
        })
    links = []
    for r in relations:
        if r["source_id"] not in id_ok or r["target_id"] not in id_ok:
            continue
        links.append({"source": r["source_id"], "target": r["target_id"],
                      "rlabel": r.get("label") or "", "text": r.get("text") or "",
                      "editorial": (r.get("sources") or []) == ["editorial_inference"]})
    return {"nodes": nodes, "links": links, "categories": labels,
            "stats": {"entities": len(nodes), "relations": len(links), "categories": len(labels)}}


HTML_TMPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__</title>
__ECHARTS_TAG__
<style>
  *{box-sizing:border-box;} html,body{margin:0;height:100%;overflow:hidden;
     font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#0b1020;color:#e5e7eb;}
  #graph{position:fixed;inset:0;z-index:0;}
  #loading{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:8;color:#93c5fd;
     font-size:14px;letter-spacing:1px;opacity:.9;}
  header{position:fixed;top:12px;left:16px;z-index:10;background:rgba(15,23,42,.82);
     padding:10px 14px;border-radius:10px;border:1px solid #1f2937;backdrop-filter:blur(4px);}
  header h1{font-size:15px;margin:0 0 4px;color:#f8fafc;}
  header .stat{font-size:12px;color:#94a3b8;}
  header .ctl{margin-top:7px;display:flex;gap:6px;}
  header .ctl button{padding:5px 9px;border:1px solid #334155;border-radius:6px;background:#0f172a;color:#cbd5e1;font-size:12px;cursor:pointer;}
  header .ctl button:hover{background:#1e293b;}
  #pathnav{position:fixed;top:132px;left:16px;z-index:10;width:210px;max-height:calc(100vh - 210px);
     overflow:auto;background:rgba(15,23,42,.86);border:1px solid #1f2937;border-radius:10px;font-size:12px;}
  #pathnav .pnhead{padding:9px 12px;font-weight:600;color:#f8fafc;cursor:pointer;position:sticky;top:0;
     background:rgba(15,23,42,.97);border-bottom:1px solid #1f2937;}
  #pnbody{padding:6px 8px;}
  #pnbody .unit{margin-bottom:5px;}
  #pnbody .uhead{color:#93c5fd;font-weight:600;padding:5px 6px;border-radius:6px;cursor:pointer;}
  #pnbody .uhead:hover{background:#1e293b;}
  #pnbody .lesson{color:#cbd5e1;padding:3px 6px 3px 18px;border-radius:6px;cursor:pointer;}
  #pnbody .lesson:hover{background:#1e293b;color:#fff;}
  #legend{position:fixed;bottom:12px;left:16px;z-index:10;background:rgba(15,23,42,.82);
     padding:10px 12px;border-radius:10px;border:1px solid #1f2937;font-size:12px;max-width:calc(100vw - 380px);
     display:flex;flex-wrap:wrap;gap:6px 12px;}
  #legend .item{display:flex;align-items:center;gap:5px;}
  #legend .dot{width:11px;height:11px;border-radius:50%;}
  #search{position:fixed;top:12px;right:356px;z-index:10;}
  #search input{padding:7px 11px;border:1px solid #334155;border-radius:7px;width:190px;
     font-size:13px;background:#0f172a;color:#e5e7eb;}
  #reset{margin-left:6px;padding:7px 10px;border:1px solid #334155;border-radius:7px;background:#0f172a;
     color:#cbd5e1;font-size:12px;cursor:pointer;}
  #side{position:fixed;top:0;right:0;width:340px;height:100vh;z-index:10;font-size:13px;
     border-left:1px solid #1f2937;padding:16px;overflow:auto;background:rgba(15,23,42,.94);}
  #side h2{font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#64748b;margin:0 0 14px;font-weight:700;}
  .muted{color:#64748b;font-size:13px;line-height:1.6;}
  .nd-accent{height:3px;border-radius:3px;margin-bottom:12px;}
  .badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;color:#0b1020;font-weight:700;letter-spacing:.5px;}
  .nd-title{font-size:19px;font-weight:700;color:#f8fafc;margin:9px 0 11px;line-height:1.3;}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;}
  .chip{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:8px;font-size:11px;background:#0b1020;border:1px solid #24324a;color:#94a3b8;}
  .chip b{color:#f8fafc;font-weight:700;}
  .card{background:#0b1020;border:1px solid #1c2942;border-radius:10px;padding:11px 12px;margin-bottom:10px;}
  .sec{font-size:11px;font-weight:700;letter-spacing:1px;color:#7c8db5;margin:0 0 9px;display:flex;align-items:center;gap:6px;}
  .sec .cnt{color:#475569;font-weight:500;}
  .desc{font-size:13px;line-height:1.75;color:#cbd5e1;white-space:pre-wrap;max-height:220px;overflow:auto;}
  .kv{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px solid #141f33;}
  .kv:last-child{border-bottom:none;} .kv .k{color:#64748b;white-space:nowrap;font-size:12px;}
  .kv .v{color:#cbd5e1;text-align:right;font-size:12px;}
  .kv .v .id{font-family:ui-monospace,Menlo,monospace;font-size:10px;color:#5b6b8c;}
  .hashwrap{display:flex;flex-wrap:wrap;gap:5px;}
  .hash{font-family:ui-monospace,Menlo,monospace;font-size:10px;color:#c4b5fd;background:#171233;
     border:1px solid #2c2350;border-radius:6px;padding:2px 7px;word-break:break-all;}
  .rel{display:flex;align-items:center;gap:7px;font-size:12px;padding:6px 0;border-bottom:1px solid #141f33;color:#cbd5e1;}
  .rel:last-child{border-bottom:none;}
  .rel .r{flex:none;color:#7dd3fc;background:#0e2438;border:1px solid #1e3a52;border-radius:6px;padding:1px 7px;font-size:10px;}
  .rel .r.edi{color:#fbbf24;background:#2a1f08;border-color:#4a3a12;}
  .rel .self{color:#8595b5;flex:none;} .rel .arrow{color:#475569;flex:none;font-size:9px;}
  .nodelink{color:#e2e8f0;cursor:pointer;border-bottom:1px dashed #33465f;}
  .nodelink:hover{color:#fff;border-bottom-color:#93c5fd;}
  .setrow{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 8px;border-radius:7px;cursor:pointer;font-size:12px;border-bottom:1px solid #141f33;}
  .setrow:last-child{border-bottom:none;} .setrow:hover{background:#152238;color:#fff;}
  .setrow .tag{font-size:10px;padding:1px 7px;border-radius:10px;color:#0b1020;font-weight:700;flex:none;}
  .click{cursor:pointer;color:#93c5fd;} .click:hover{color:#fff;}
</style>
</head>
<body>
<div id="graph"></div>
<div id="loading">✦ 正在生成知识图谱布局…</div>
<header>
  <h1>__TITLE__</h1>
  <div class="stat">实体 <b>__N_ENT__</b> · 关系 <b>__N_REL__</b> · 类型 <b>__N_CAT__</b></div>
  <div class="ctl"><button id="btnReplay">▶ 重播生长</button></div>
</header>
<div id="search"><input id="q" placeholder="搜索节点→回车聚焦…" autocomplete="off"/><button id="reset">重置</button></div>
<div id="pathnav">
  <div class="pnhead" id="pnhead">🧭 学习路径导览（点击聚焦） ▾</div>
  <div id="pnbody"></div>
</div>
<div id="legend"></div>
<div id="side">
  <h2>节点详情</h2>
  <div id="detail" class="muted">拖拽平移 · 滚轮缩放 · 点击节点：高亮该节点及其上下级关联，连线上显示关系名。滚轮放大后显示全部节点名；悬停节点/连线可看详情。左侧「学习路径导览」按单元/课时聚焦。点击空白处重置。</div>
</div>
<script>
var DATA = __DATA__;
var PATH = __PATH__;
var unitName={}, lessonTitle={};
PATH.forEach(function(u){ unitName[u.unitId]=u.name; (u.lessons||[]).forEach(function(l){ lessonTitle[l.lessonId]=l.title; }); });
var palette = ['#60a5fa','#fbbf24','#34d399','#f87171','#22d3ee','#c084fc',
               '#fb923c','#4ade80','#f472b6','#38bdf8','#a3e635','#e879f9'];
var DIM = '#1e3a52';
// 层级体系：depth = 距根节点图距离，仅决定节点尺寸与生长顺序；
// 节点颜色按类型（categories/label_text）区分 —— 层级只是属性，不是着色维度
var LVL_NAMES=['核心 · 根节点','一级概念','二级概念','三级概念','外围知识'];
var LVL_SIZES=[46,32,22,15,11];
function lvl(n){ return Math.min(4, (n.depth==null)?4:n.depth); }
function typeColor(n){ return palette[n.category%palette.length]; }
// 关系类型配色（聚焦时边标签着色，一眼区分 包含/属于/表现为…）
var RCOLOR=__RCOLOR__;
var nodeById={}; DATA.nodes.forEach(function(n){ nodeById[n.id]=n; });
var relByNode={};
DATA.links.forEach(function(l){
  var s=l.source, t=l.target;
  (relByNode[s]=relByNode[s]||[]).push({dir:'out',other:t,rl:l.rlabel,edi:l.editorial,link:l});
  (relByNode[t]=relByNode[t]||[]).push({dir:'in',other:s,rl:l.rlabel,edi:l.editorial,link:l});
});
function esc(s){return (s||'').replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function idOf(e){ return (typeof e==='object'&&e)?e.id:e; }
function sz(n){ return LVL_SIZES[lvl(n)] + Math.min(10, n.val*1.5); }
function pct(arr,p){ var s=arr.slice().sort(function(a,b){return a-b;}); return s[Math.max(0,Math.min(s.length-1,Math.floor(p/100*(s.length-1))))]; }
function pct(arr,p){ var s=arr.slice().sort(function(a,b){return a-b;}); return s[Math.max(0,Math.min(s.length-1,Math.floor(p/100*(s.length-1))))]; }
function fitView(){
  // 百分位包围盒自适应 + 避开右侧详情面板；图占可视区 ~90%
  var xs=DATA.nodes.map(function(n){return n.x;}), ys=DATA.nodes.map(function(n){return n.y;});
  var x0=pct(xs,2), x1=pct(xs,98), y0=pct(ys,2), y1=pct(ys,98);
  var cx=(x0+x1)/2, cy=(y0+y1)/2;
  var bw=(x1-x0)||1, bh=(y1-y0)||1;
  var vw=chart.getWidth(), vh=chart.getHeight();
  var pw=340;                                  // 右侧详情面板宽度
  var availW=Math.max(400, vw-pw), availH=Math.max(300, vh-70);
  var z=Math.max(0.4, Math.min(1.6, Math.min(availW/bw, availH/bh)*0.9));
  // center 语义：映射到画布中心的布局坐标点；把内容中心平移到可视区中心（避开面板）
  chart.setOption({series:[{id:'g', center:[cx+pw/(2*z), cy], zoom:z}]});
  // 同步缩放阈值标签开关（总览缩回时必须关掉全量标签，否则全部名称糊成一团）
  var wantAll=z>=1.5;
  if(wantAll!==labelAll){ labelAll=wantAll; apply(); }
}

var focusId=null, hlNodes=new Set(), hlLinks=new Set();
var labelAll=false;                       // 放大后显示全部节点名，总览只标核心层
var savedPos={};                          // 聚焦径向重排前的原始坐标
function isOn(id){ return !focusId || hlNodes.has(id); }
function showNodeLabel(n,on){
  // 总览只标注核心层；聚焦后标注当前邻域；放大 (zoom≥1.5) 后标注全部
  return !!on && (labelAll || !!focusId || lvl(n)<=1);
}
function focusRelayout(hubId){
  // 聚焦时把出边邻居径向均匀重排（按当前角度排序，避免边交叉），解决星型拓扑挤在一起
  var rel=relByNode[hubId]||[];   // 双向邻居（出+入）都参与径向重排
  if(rel.length<2) return;
  var h=nodeById[hubId];
  var items=rel.map(function(x){ var n=nodeById[x.other]; if(!n||savedPos[x.other]) return null;
      return {id:x.other, a:Math.atan2(n.y-h.y, n.x-h.x)}; }).filter(Boolean);
  if(items.length<2) return;
  items.sort(function(p,q){return p.a-q.a;});
  var R=Math.min(260, 42+18*items.length);
  var a0=items[0].a;
  savedPos[hubId]=savedPos[hubId]||{x:h.x,y:h.y};
  items.forEach(function(it,i){
    var ang=a0+2*Math.PI*i/items.length;
    var n=nodeById[it.id];
    savedPos[it.id]={x:n.x,y:n.y};
    n.x=h.x+R*Math.cos(ang); n.y=h.y+R*Math.sin(ang);
  });
}
function restorePos(){
  Object.keys(savedPos).forEach(function(id){ var p=savedPos[id]; if(nodeById[id]){ nodeById[id].x=p.x; nodeById[id].y=p.y; } });
  savedPos={};
}

var chart = echarts.init(document.getElementById('graph'), null, {renderer:'canvas'});
chart.setOption({
  backgroundColor:'#0b1020',
  tooltip:{ trigger:'item', confine:true, backgroundColor:'#0f172a', borderColor:'#334155',
    textStyle:{color:'#e5e7eb',fontSize:12},
    formatter:function(p){
      if(p.dataType==='edge'){ var l=p.data.__l; return '<b>'+esc(l.rlabel)+'</b>'+(l.editorial?' <span style=\"color:#fbbf24\">[编辑推断]</span>':''); }
      var n=nodeById[p.data.id]; if(!n) return '';
      return '<b>'+esc(n.name)+'</b> <span style=\"color:#94a3b8\">['+esc(n.label_text)+']</span> <span style=\"color:#64748b\">· '+esc(LVL_NAMES[lvl(n)])+'</span>'
        + (n.desc?'<br><span style=\"color:#cbd5e1;font-size:11px\">'+esc(n.desc.slice(0,80))+(n.desc.length>80?'…':'')+'</span>':'');
    }},
  animation:true, animationDuration:600, animationEasing:'cubicOut',
  animationDurationUpdate:500, animationEasingUpdate:'cubicOut',
  series:[{ id:'g', type:'graph', layout:'none', roam:true, zoom:0.85, center:[0,0],
    nodeScaleRatio:0.45, focusNodeAdjacency:false,
    edgeLabel:{ show:false, fontSize:10, color:'#dbe4f5', backgroundColor:'rgba(11,16,32,0.9)', padding:[2,5], borderRadius:3,
                formatter:function(p){ var l=(p.data&&p.data.__l)||p.data; return (l&&l.rlabel)?l.rlabel:''; } },
    data:[], links:[], emphasis:{scale:false, focus:'none'} }]
});

function apply(){
  var ns=[], ls=[];
  var fn=(focusId && focusId!=='__set__') ? nodeById[focusId] : null;
  DATA.nodes.forEach(function(n){ if(!shownIds.has(n.id)) return; var on=isOn(n.id);
    // 聚焦时节点标签按相对方位避让，减少与焦点/边标签重叠
    var lpos='right';
    if(fn && n.id!==fn.id){
      var dx=n.x-fn.x, dy=n.y-fn.y;
      if(Math.abs(dx)>Math.abs(dy)*0.7) lpos=dx>0?'right':'left';
      else lpos=dy>0?'bottom':'top';
    }
    ns.push({ id:n.id, name:n.id, value:n.val, x:n.x, y:n.y, symbolSize:sz(n),
      itemStyle:{ color: on?typeColor(n):DIM, opacity: on?1:0.12,
                  borderColor:'#0b1020', borderWidth:1 },
      label:{ show:showNodeLabel(n,on), formatter:n.name, color:'#e5e7eb', fontSize:12, position:lpos, distance:4,
              backgroundColor:on?'rgba(11,16,32,0.72)':'transparent', padding:[1,3], borderRadius:3 } });
  });
  // 高亮边分组：同一关系名 >2 条时只标注最长一条，避免“包含 包含 包含”堆叠
  var hlGroups={}, hlBest={};
  DATA.links.forEach(function(l){ if(!hlLinks.has(l)) return;
    hlGroups[l.rlabel]=(hlGroups[l.rlabel]||0)+1;
    var s=nodeById[l.source], t=nodeById[l.target];
    var len=s&&t?Math.hypot(s.x-t.x,s.y-t.y):0;
    if(!hlBest[l.rlabel]||len>hlBest[l.rlabel].len) hlBest[l.rlabel]={len:len,l:l};
  });
  // 关系名胶囊：画在边中点的隐形合成节点上 —— 节点标签天然水平、跟随缩放平移；
  // 边标签的 labelLayout 水平化在增量 setOption 下会把标签错位到画布原点（实测），故弃用边标签
  var midNodes=[];
  DATA.links.forEach(function(l){ if(!shownIds.has(l.source)||!shownIds.has(l.target)) return; var hl=hlLinks.has(l);
    // 关系名可见性：仅聚焦后显示（点击节点/单元），总览保持干净；总览里悬停连线可看关系名
    var showLbl;
    if(focusId==='__set__') showLbl = hl && (hlGroups[l.rlabel]<=2 || hlBest[l.rlabel].l===l);
    else showLbl = !!focusId && hl;
    ls.push({ source:l.source, target:l.target, __l:l, rlabel:l.rlabel,
      lineStyle:{ color: hl?'#ffffff':(l.editorial?'rgba(251,146,60,0.7)':'rgba(180,196,224,0.5)'),
                  width: hl?2.4:0.8, opacity:(focusId&&!hl)?0.12:1, curveness:0.1 },
      symbol:['none','arrow'], symbolSize:[0,7] });
    if(showLbl){ var s=nodeById[l.source], t=nodeById[l.target];
      if(s&&t) midNodes.push({ id:'__mid_'+l.source+'→'+l.target, x:(s.x+t.x)/2, y:(s.y+t.y)/2,
        symbolSize:0, silent:true, itemStyle:{opacity:0, color:'transparent'},
        label:{ show:true, opacity:1, position:'top', distance:2, fontSize:12, color:RCOLOR[l.rlabel]||'#e2e8f0',
                formatter:l.rlabel, backgroundColor:'rgba(8,12,24,0.92)', borderColor:'#3b4a63', borderWidth:1,
                padding:[2,6], borderRadius:4 } }); }
  });
  chart.setOption({ series:[{ id:'g', data:ns.concat(midNodes), links:ls }] });
}
function centerOn(ids, zoom){
  var pts=DATA.nodes.filter(function(n){return ids.indexOf?ids.indexOf(n.id)>=0:ids.has(n.id);});
  if(!pts.length) return;
  var cx=0,cy=0; pts.forEach(function(p){cx+=p.x;cy+=p.y;}); cx/=pts.length; cy/=pts.length;
  if(zoom){ chart.setOption({series:[{id:'g', center:[cx,cy], zoom:zoom}]}); return; }
  // 自适应：按 p90 半径适配主体邻域（个别远郊离群点允许出屏），铺满视口短边 ~84%
  var rads=pts.map(function(p){return Math.hypot(p.x-cx,p.y-cy);}).sort(function(a,b){return a-b;});
  var maxd=rads[Math.max(0,Math.min(rads.length-1,Math.floor(0.9*rads.length)))]||100;
  var vw=chart.getWidth(), vh=chart.getHeight();
  var z=Math.max(1.4, Math.min(5.0, Math.min(vw,vh)*0.42/(maxd+30)));
  chart.setOption({series:[{id:'g', center:[cx,cy], zoom:z}]});
}

var detailHome=document.getElementById('detail').innerHTML;
function updateFocus(node){
  focusId = node ? node.id : null;
  hlNodes.clear(); hlLinks.clear();
  if(node){
    hlNodes.add(node.id);
    (relByNode[node.id]||[]).forEach(function(x){ hlNodes.add(x.other); hlLinks.add(x.link); });  // 双向：出边+入边都高亮，连线才能看到关系名
  } else {
    if(Object.keys(savedPos).length){ restorePos(); fitView(); }
    var d=document.getElementById('detail'); d.className='muted'; d.innerHTML=detailHome;  // 重置详情面板
  }
  apply();
}
function focusSet(ids, title){
  finishGrowthNow();
  focusId='__set__'; hlNodes=new Set(ids); hlLinks=new Set();
  DATA.links.forEach(function(l){ if(hlNodes.has(l.source)&&hlNodes.has(l.target)) hlLinks.add(l); });
  apply(); centerOn(ids);
  var setHtml=ids.map(function(id){var n=nodeById[id]; var c=palette[n.category%palette.length];
    return '<div class="setrow" onclick="focusById(\''+id+'\')"><span>'+esc(n.name)+'</span>'
         + '<span class="tag" style="background:'+c+'">'+esc(n.label_text)+'</span></div>';}).join('');
  var d=document.getElementById('detail'); d.className='';
  d.innerHTML=
      '<div class="nd-accent" style="background:linear-gradient(90deg,#60a5fa,#22d3ee)"></div>'
    + '<div class="nd-title" style="font-size:16px">'+esc(title)+'</div>'
    + '<div class="chips"><span class="chip">📍 知识节点 <b>'+ids.length+'</b></span></div>'
    + '<div class="card"><div class="sec">📋 该范围知识节点 <span class="cnt">点击下钻</span></div>'+setHtml+'</div>';
}
function focusById(id){ var n=nodeById[id]; if(n){ finishGrowthNow(); restorePos();
  focusRelayout(n.id);
  var ids=[n.id].concat((relByNode[n.id]||[]).map(function(x){return x.other;}));
  centerOn(ids); updateFocus(n); showDetail(n); } }
window.focusById=focusById;
function showDetail(n){
  var col=palette[n.category%palette.length];
  function relRow(x){
    var o=nodeById[x.other]; var on=o?o.name:x.other; var rcls='r'+(x.edi?' edi':'');
    var link='<span class="nodelink" onclick="focusById(\''+x.other+'\')">'+esc(on)+'</span>';
    return x.dir==='out'
      ? '<div class="rel"><span class="self">本节点</span><span class="'+rcls+'">'+esc(x.rl)+'</span><span class="arrow">▶</span>'+link+'</div>'
      : '<div class="rel">'+link+'<span class="'+rcls+'">'+esc(x.rl)+'</span><span class="arrow">▶</span><span class="self">本节点</span></div>';
  }
  var outs=(relByNode[n.id]||[]).filter(function(x){return x.dir==='out';});
  var ins=(relByNode[n.id]||[]).filter(function(x){return x.dir==='in';});
  var relHtml='';
  if(outs.length) relHtml+='<div class="sec">↳ 下一级 · 出边 <span class="cnt">'+outs.length+'</span></div>'+outs.map(relRow).join('');
  if(ins.length) relHtml+='<div class="sec" style="margin-top:12px">↰ 上级 / 关联 · 入边 <span class="cnt">'+ins.length+'</span></div>'+ins.map(relRow).join('');
  if(!outs.length && !ins.length) relHtml='<div class="muted">（无关联关系）</div>';
  var hashes=(n.hashes||[]).map(function(x){return '<span class="hash">'+esc(x)+'</span>';}).join('')||'<span class="muted">无</span>';
  function kvNames(k, items, nameMap){
    var v=(items&&items.length)? items.map(function(id){ var nm=nameMap[id];
        return nm? '<div>'+esc(nm)+'<br><span class="id">'+esc(id)+'</span></div>' : '<div class="id">'+esc(id)+'</div>'; }).join('') : '—';
    return '<div class="kv"><span class="k">'+k+'</span><span class="v">'+v+'</span></div>';
  }
  var meta='<div class="kv"><span class="k">教材 textbookId</span><span class="v"><span class="id">'+esc(n.textbook||'—')+'</span></span></div>'
    + kvNames('单元 unitId', n.units, unitName)
    + kvNames('课时 lessonId', n.lessons, lessonTitle);
  var d=document.getElementById('detail'); d.className='';
  d.innerHTML=
      '<div class="nd-accent" style="background:'+col+'"></div>'
    + '<span class="badge" style="background:'+col+'">'+esc(n.label_text)+'</span>'
    + '<div class="nd-title">'+esc(n.name)+'</div>'
    + '<div class="chips"><span class="chip">🔗 度数 <b>'+n.val+'</b></span>'
    +   '<span class="chip">▶ 子节点 <b>'+outs.length+'</b></span>'
    +   '<span class="chip">＃ data-hash <b>'+(n.hashes||[]).length+'</b></span></div>'
    + (n.desc? '<div class="card"><div class="sec">📖 原文摘录</div><div class="desc">'+esc(n.desc)+'</div></div>' : '')
    + '<div class="card"><div class="sec">🏷 归属元数据</div>'+meta+'</div>'
    + '<div class="card"><div class="sec">＃ data-hash 溯源 <span class="cnt">'+(n.hashes||[]).length+'</span></div><div class="hashwrap">'+hashes+'</div></div>'
    + '<div class="card"><div class="sec">🕸 关联关系 <span class="cnt">'+((relByNode[n.id]||[]).length)+'</span></div>'+relHtml+'</div>';
}

// ══════════ 生长：逐层揭示（位置已由 Python 钉定，仅进入动画，无力扰动） ══════════
var LAYER_MS=430;
var adjU={}; DATA.links.forEach(function(l){ (adjU[l.source]=adjU[l.source]||[]).push(l.target); (adjU[l.target]=adjU[l.target]||[]).push(l.source); });
var rootId=DATA.nodes.reduce(function(a,b){return b.val>a.val?b:a;}, DATA.nodes[0]).id;
function bfsLayers(root){
  var seen=new Set([root]), layers=[[root]], fr=[root];
  while(fr.length){ var nx=[]; fr.forEach(function(id){ (adjU[id]||[]).forEach(function(o){ if(!seen.has(o)){seen.add(o);nx.push(o);} }); }); if(nx.length)layers.push(nx); fr=nx; }
  var rest=DATA.nodes.filter(function(n){return !seen.has(n.id);}).map(function(n){return n.id;});
  if(rest.length)layers.push(rest);
  return layers;
}
var LAYERS=bfsLayers(rootId), shownIds=new Set(), revealTimer=null;
function beginReveal(){
  document.getElementById('loading').style.display='none';
  if(revealTimer){clearInterval(revealTimer);revealTimer=null;}
  focusId=null; hlNodes.clear(); hlLinks.clear();
  shownIds=new Set(); var step=0;
  LAYERS[0].forEach(function(id){shownIds.add(id);}); apply(); step=1;
  fitView();
  revealTimer=setInterval(function(){
    if(step>=LAYERS.length){ clearInterval(revealTimer); revealTimer=null; fitView(); return; }
    LAYERS[step].forEach(function(id){shownIds.add(id);}); apply(); step++;
  }, LAYER_MS);
}
function finishGrowthNow(){
  if(revealTimer){clearInterval(revealTimer);revealTimer=null;}
  var was=shownIds.size;
  DATA.nodes.forEach(function(n){shownIds.add(n.id);});
  if(shownIds.size!==was) apply();
}

// ── 交互 ──
chart.on('click', function(p){ if(p.dataType==='node'){ focusById(p.data.id); } });
chart.getZr().on('click', function(e){ if(!e.target){ updateFocus(null); } });
// 缩放时按 zoom 阈值切换全量节点名显示（防抖）
var roamT=null;
chart.on('graphroam', function(){
  if(roamT) clearTimeout(roamT);
  roamT=setTimeout(function(){
    var z=chart.getOption().series[0].zoom;
    var want=z>=1.5;
    if(want!==labelAll){ labelAll=want; apply(); }
  }, 250);
});

// ── 学习路径导览 ──
var pnbody=document.getElementById('pnbody');
PATH.forEach(function(u){
  var ud=document.createElement('div'); ud.className='unit';
  var uh=document.createElement('div'); uh.className='uhead'; uh.textContent=u.name;
  uh.onclick=function(){ var ids=DATA.nodes.filter(function(n){return (n.units||[]).indexOf(u.unitId)>=0;}).map(function(n){return n.id;});
    if(ids.length) focusSet(ids, u.name+'（整单元 '+ids.length+' 节点）'); };
  ud.appendChild(uh);
  u.lessons.forEach(function(l){
    var ld=document.createElement('div'); ld.className='lesson'; ld.textContent='· '+l.title;
    ld.onclick=function(){ var ids=DATA.nodes.filter(function(n){return (n.lessons||[]).indexOf(l.lessonId)>=0;}).map(function(n){return n.id;});
      if(ids.length) focusSet(ids, u.name+' / '+l.title); };
    ud.appendChild(ld);
  });
  pnbody.appendChild(ud);
});
document.getElementById('pnhead').onclick=function(){ var b=document.getElementById('pnbody'); b.style.display=(b.style.display==='none')?'block':'none'; };

var lg=document.getElementById('legend');
(function(){
  var catCnt={};
  DATA.nodes.forEach(function(n){ catCnt[n.category]=(catCnt[n.category]||0)+1; });
  DATA.categories.forEach(function(name,i){ var d=document.createElement('div'); d.className='item';
    var dot=document.createElement('span'); dot.className='dot'; dot.style.background=palette[i%palette.length];
    d.appendChild(dot); d.appendChild(document.createTextNode(name+' '+(catCnt[i]||0))); lg.appendChild(d); });
})();

// ── 控件 ──
document.getElementById('btnReplay').onclick=beginReveal;
document.getElementById('q').addEventListener('keydown',function(e){
  if(e.key!=='Enter') return; var q=e.target.value.trim(); if(!q) return;
  var hit=DATA.nodes.find(function(n){return n.name.indexOf(q)>=0 || (n.label_text||'').indexOf(q)>=0;});
  if(hit){ focusById(hit.id); }
});
document.getElementById('reset').addEventListener('click',function(){ updateFocus(null); document.getElementById('q').value=''; fitView(); });
window.addEventListener('resize',function(){ chart.resize(); });

// 启动生长
beginReveal();
</script>
</body>
</html>
"""


# 关系配色默认值（未列出的关系在前端回退为 #e2e8f0）；用 --rel-colors 覆盖或扩充
DEFAULT_REL_COLORS = {
    "包含": "#7dd3fc", "属于": "#fbbf24", "表现为": "#86efac", "导致": "#fca5a5",
    "影响": "#c4b5fd", "调适": "#fdba74", "培养": "#5eead4",
    "应用于": "#5eead4", "提出": "#5eead4",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--title", default="知识图谱")
    ap.add_argument("--path-json", default="", help="学习路径 JSON（[{unitId,name,lessons:[{lessonId,title}]}]）")
    ap.add_argument("--echarts-js", default="",
                    help="本地 echarts.min.js 路径；留空自动探测（输出目录 → 脚本目录 → skill assets/）")
    ap.add_argument("--cdn", action="store_true",
                    help="强制 CDN 加载 echarts，不内联（产物体积小但需联网）")
    ap.add_argument("--rel-colors", default="",
                    help='关系配色覆盖：JSON 文件路径或内联 JSON，如 \'{"包含":"#7dd3fc"}\'')
    args = ap.parse_args()
    graph = json.loads(Path(args.graph).read_text(encoding="utf-8"))
    p = build_payload(graph)
    path_data = []
    if args.path_json and Path(args.path_json).exists():
        path_data = json.loads(Path(args.path_json).read_text(encoding="utf-8"))
    # 关系配色：默认值 + 可选覆盖（文件路径或内联 JSON）
    rel_colors = dict(DEFAULT_REL_COLORS)
    if args.rel_colors:
        raw = (Path(args.rel_colors).read_text(encoding="utf-8")
               if Path(args.rel_colors).exists() else args.rel_colors)
        rel_colors.update(json.loads(raw))
    # 内联 echarts（零 CDN 依赖）：输出目录 → 脚本目录 → skill assets/；--cdn 或都找不到则回退 CDN
    _here = Path(__file__).resolve().parent
    candidates = [args.echarts_js] if args.echarts_js else [
        str(Path(args.output).resolve().parent / "echarts.min.js"),
        str(_here / "echarts.min.js"),
        str(_here.parent / "assets" / "echarts.min.js")]
    echarts_tag = '<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>'
    if not args.cdn:
        for c in candidates:
            if c and Path(c).exists():
                js = Path(c).read_text(encoding="utf-8").replace("</script>", "<\\/script>")
                echarts_tag = "<script>\n" + js + "\n</script>"
                break
    out = (HTML_TMPL
           .replace("__TITLE__", html.escape(args.title))
           .replace("__N_ENT__", str(p["stats"]["entities"]))
           .replace("__N_REL__", str(p["stats"]["relations"]))
           .replace("__N_CAT__", str(p["stats"]["categories"]))
           .replace("__PATH__", json.dumps(path_data, ensure_ascii=False))
           .replace("__DATA__", json.dumps(p, ensure_ascii=False))
           .replace("__RCOLOR__", json.dumps(rel_colors, ensure_ascii=False))
           .replace("__ECHARTS_TAG__", echarts_tag))
    Path(args.output).write_text(out, encoding="utf-8")
    print(f"ok: {p['stats']['entities']} nodes, {p['stats']['relations']} edges, "
          f"{len(path_data)} units -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
