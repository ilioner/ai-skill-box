#!/usr/bin/env python3
"""把 graph.json 渲染成 3D 球状力导向知识图谱（3d-force-graph / Three.js）。

用法：
  python3 render_graph_3d.py --graph graph.json --output graph_3d.html \
      --title "标题" --path-json learning_path.json

特性：
- 节点常驻文字标签、按实体 label 分类着色、节点大小=度数；
- 连线细线（发丝线）高对比、关系类型常驻标注在连线中点；
- 加载时优雅「生长」：先隐藏布局→钉住位置→逐层透明度渐入（缓动），力模拟不再扰动；
- 左侧「学习路径导览」按 unitId/lessonId 组织，点击单元/课时聚焦对应节点集；
- 点击节点聚焦（仅高亮该节点及其下一级子节点，其余淡化）；无自动旋转；
- 详情面板显示类型/原文描述/data-hash/单元·课时元数据/关联关系。
"""
from __future__ import annotations
import argparse, json, html
from pathlib import Path


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

    def attrs_of(e, lab):
        return [a["text"] for a in (e.get("attributes") or []) if a.get("label") == lab]

    nodes = []
    for e in entities:
        d = deg.get(e["id"], 0)
        nodes.append({
            "id": e["id"], "name": e["text"], "category": cat_index[e["label"]],
            "label_text": e["label"], "val": max(1, d),
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
<script src="https://cdn.jsdelivr.net/npm/three@0.157.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three-spritetext@1.8.2/dist/three-spritetext.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/3d-force-graph@1.73.4/dist/3d-force-graph.min.js"></script>
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
  #side{position:fixed;top:0;right:0;width:340px;height:100vh;z-index:10;
     border-left:1px solid #1f2937;padding:16px;overflow:auto;background:rgba(15,23,42,.94);}
  #side{font-size:13px;}
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
  <div id="detail" class="muted">拖拽旋转 · 滚轮缩放 · 点击节点：仅高亮该节点与其下一级子节点。左侧「学习路径导览」按单元/课时聚焦。点击空白处重置。</div>
</div>
<script>
var DATA = __DATA__;
var PATH = __PATH__;
var unitName={}, lessonTitle={};
PATH.forEach(function(u){ unitName[u.unitId]=u.name; (u.lessons||[]).forEach(function(l){ lessonTitle[l.lessonId]=l.title; }); });
var palette = ['#60a5fa','#34d399','#fbbf24','#f87171','#22d3ee','#4ade80','#fb923c',
               '#a78bfa','#f472b6','#e879f9','#2dd4bf','#818cf8','#facc15'];
var DIM = '#1e3a52';
var nodeById={}; DATA.nodes.forEach(function(n){ nodeById[n.id]=n; });
var relByNode={};
DATA.links.forEach(function(l){
  var s=(typeof l.source==='object')?l.source.id:l.source, t=(typeof l.target==='object')?l.target.id:l.target;
  (relByNode[s]=relByNode[s]||[]).push({dir:'out',other:t,rl:l.rlabel,edi:l.editorial});
  (relByNode[t]=relByNode[t]||[]).push({dir:'in',other:s,rl:l.rlabel,edi:l.editorial});
});
function esc(s){return (s||'').replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function idOf(e){ return (typeof e==='object' && e) ? e.id : e; }

var focusId=null, hlNodes=new Set(), hlLinks=new Set();
function isOn(id){ return !focusId || hlNodes.has(id); }
function ncolor(n){ return isOn(n.id) ? palette[n.category%palette.length] : DIM; }
function lcolor(l){
  if(hlLinks.has(l)) return '#ffffff';
  return l.editorial ? 'rgba(251,146,60,0.75)' : 'rgba(180,196,224,0.65)';
}
function lwidth(l){ return hlLinks.has(l) ? 1.4 : 0; }

var Graph = ForceGraph3D({ controlType:'orbit' })(document.getElementById('graph'))
  .width(window.innerWidth).height(window.innerHeight)
  .backgroundColor('#0b1020')
  .showNavInfo(false)
  .cooldownTicks(90)
  .nodeRelSize(4)
  .nodeVal(function(n){ return n.val; })
  .nodeColor(ncolor)
  .nodeOpacity(0.95)
  .nodeThreeObjectExtend(true)
  .nodeThreeObject(function(n){
    var s=new SpriteText(n.name);
    s.color='#eef2ff'; s.textHeight=3.4; s.fontFace='PingFang SC, Microsoft YaHei, sans-serif';
    s.strokeWidth=0.6; s.strokeColor='#0b1020'; s.padding=0.6;
    s.position.set(0, Math.cbrt(n.val)*4 + 6, 0);
    s.material.transparent=true;
    n.__label=s; return s;
  })
  .linkColor(lcolor).linkWidth(lwidth)
  .linkDirectionalArrowLength(2.0).linkDirectionalArrowRelPos(1)
  .linkThreeObjectExtend(true)
  .linkThreeObject(function(l){
    var s=new SpriteText(l.rlabel);
    s.color=l.editorial?'#fb923c':'#94a3b8'; s.textHeight=2.2;
    s.fontFace='PingFang SC, Microsoft YaHei, sans-serif';
    s.strokeWidth=0.3; s.strokeColor='#0b1020'; s.padding=0.4; s.material.transparent=true;
    l.__linkLabel=s; return s;
  })
  .linkPositionUpdate(function(s,coords){
    if(!s) return;
    s.position.x=(coords.start.x+coords.end.x)/2;
    s.position.y=(coords.start.y+coords.end.y)/2;
    s.position.z=(coords.start.z+coords.end.z)/2;
  })
  .onNodeClick(function(node){ finishGrowthNow(); focusNode(node); updateFocus(node); showDetail(node); })
  .onBackgroundClick(function(){ updateFocus(null); });

Graph.d3Force('charge').strength(-90);
var oc=Graph.controls(); oc.autoRotate=false;

function refreshStyles(){
  Graph.nodeColor(ncolor).linkColor(lcolor).linkWidth(lwidth);
  DATA.nodes.forEach(function(n){ if(n.__label){ n.__label.visible=isOn(n.id); n.__label.material.opacity=isOn(n.id)?1:0.15; }});
  (Graph.graphData().links||[]).forEach(function(l){ if(l.__linkLabel){
    var on=!focusId||hlLinks.has(l); l.__linkLabel.visible=on; l.__linkLabel.material.opacity=hlLinks.has(l)?1:(focusId?0.12:0.9);
  }});
}
function focusNode(node){
  var d=90, r=Math.hypot(node.x,node.y,node.z)||1, k=1+d/r;
  Graph.cameraPosition({x:node.x*k,y:node.y*k,z:node.z*k}, node, 800);
}
function updateFocus(node){
  focusId = node ? node.id : null;
  hlNodes.clear(); hlLinks.clear();
  if(node){
    hlNodes.add(node.id);
    (relByNode[node.id]||[]).forEach(function(x){ if(x.dir==='out') hlNodes.add(x.other); });
    (Graph.graphData().links||[]).forEach(function(l){
      if(idOf(l.source)===node.id && hlNodes.has(idOf(l.target))) hlLinks.add(l);
    });
  }
  refreshStyles();
}
function focusSet(ids, title){
  finishGrowthNow();
  focusId='__set__'; hlNodes=new Set(ids); hlLinks=new Set();
  (Graph.graphData().links||[]).forEach(function(l){
    if(hlNodes.has(idOf(l.source)) && hlNodes.has(idOf(l.target))) hlLinks.add(l);
  });
  refreshStyles();
  var pts=DATA.nodes.filter(function(n){ return hlNodes.has(n.id) && n.x!=null; });
  if(pts.length){
    var cx=0,cy=0,cz=0; pts.forEach(function(p){cx+=p.x;cy+=p.y;cz+=p.z;}); cx/=pts.length;cy/=pts.length;cz/=pts.length;
    var maxd=Math.max.apply(null, pts.map(function(p){return Math.hypot(p.x-cx,p.y-cy,p.z-cz);}))||60;
    var dist=maxd*2.2+120;
    Graph.cameraPosition({x:cx,y:cy,z:cz+dist},{x:cx,y:cy,z:cz},900);
  }
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
function focusById(id){ var n=nodeById[id]; if(n){ finishGrowthNow(); focusNode(n); updateFocus(n); showDetail(n); } }
window.focusById=focusById;
function showDetail(n){
  var col=palette[n.category%palette.length];
  function relRow(x){
    var o=nodeById[x.other]; var on=o?o.name:x.other;
    var rcls='r'+(x.edi?' edi':'');
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

// ══════════ 优雅生长：钉住布局 + 逐层透明度渐入 ══════════
var FADE_MS=750, LAYER_MS=430;
function easeOut(a){ return 1-Math.pow(1-a,3); }
function setNodeOpacity(n,o){
  if(n.__threeObj){ n.__threeObj.traverse(function(c){ if(c.isMesh&&c.material){ c.material.transparent=true; c.material.opacity=0.95*o; }}); }
  if(n.__label&&n.__label.material){ n.__label.material.transparent=true; n.__label.material.opacity=o; n.__label.visible=o>0.02; }
}
function setLinkOpacity(l,o){
  if(l.__lineObj&&l.__lineObj.material){ l.__lineObj.material.transparent=true; l.__lineObj.material.opacity=(l.editorial?0.75:0.65)*o; }
  if(l.__linkLabel&&l.__linkLabel.material){ l.__linkLabel.material.transparent=true; l.__linkLabel.material.opacity=0.9*o; l.__linkLabel.visible=o>0.02; }
}
var adjU={}; DATA.links.forEach(function(l){var s=idOf(l.source),t=idOf(l.target);(adjU[s]=adjU[s]||[]).push(t);(adjU[t]=adjU[t]||[]).push(s);});
var rootId=DATA.nodes.reduce(function(a,b){return b.val>a.val?b:a;}, DATA.nodes[0]).id;
function bfsLayers(root){
  var seen=new Set([root]), layers=[[root]], fr=[root];
  while(fr.length){ var nx=[]; fr.forEach(function(id){ (adjU[id]||[]).forEach(function(o){ if(!seen.has(o)){seen.add(o);nx.push(o);} }); }); if(nx.length)layers.push(nx); fr=nx; }
  var rest=DATA.nodes.filter(function(n){return !seen.has(n.id);}).map(function(n){return n.id;});
  if(rest.length)layers.push(rest);
  return layers;
}
var LAYERS=bfsLayers(rootId);
var shownIds=new Set(), revealTimer=null, rafOn=false, pinned=false;

function fadeFrame(){
  if(!rafOn) return;
  var now=performance.now(), fading=false;
  DATA.nodes.forEach(function(n){
    if(shownIds.has(n.id)){ if(n.__born===undefined)n.__born=now; var o=easeOut(Math.min(1,(now-n.__born)/FADE_MS)); setNodeOpacity(n,o); if(o<1)fading=true; }
    else setNodeOpacity(n,0);
  });
  (Graph.graphData().links||[]).forEach(function(l){
    var s=idOf(l.source),t=idOf(l.target);
    if(shownIds.has(s)&&shownIds.has(t)){ if(l.__born===undefined)l.__born=now; var o=easeOut(Math.min(1,(now-l.__born)/FADE_MS)); setLinkOpacity(l,o); if(o<1)fading=true; }
    else setLinkOpacity(l,0);
  });
  var growing = revealTimer!==null;
  if(!growing && !fading && shownIds.size>=DATA.nodes.length){ rafOn=false; return; }  // 生长完成，交还常规样式
  requestAnimationFrame(fadeFrame);
}
function beginReveal(){
  document.getElementById('loading').style.display='none';
  if(revealTimer){clearInterval(revealTimer);revealTimer=null;}
  updateFocus(null);
  shownIds=new Set();
  DATA.nodes.forEach(function(n){ delete n.__born; });
  DATA.links.forEach(function(l){ delete l.__born; });
  if(!rafOn){ rafOn=true; requestAnimationFrame(fadeFrame); }
  var step=0;
  LAYERS[0].forEach(function(id){shownIds.add(id);}); step=1;
  revealTimer=setInterval(function(){
    if(step>=LAYERS.length){ clearInterval(revealTimer); revealTimer=null; return; }
    LAYERS[step].forEach(function(id){shownIds.add(id);}); step++;
  }, LAYER_MS);
}
function finishGrowthNow(){
  if(revealTimer){clearInterval(revealTimer);revealTimer=null;}
  rafOn=false;  // 停止渐入循环，交还常规样式
  DATA.nodes.forEach(function(n){ shownIds.add(n.id); setNodeOpacity(n,1); });
  (Graph.graphData().links||[]).forEach(function(l){ setLinkOpacity(l,1); });
}

// 预布局：全量入图但透明，钉住位置后开始逐层渐入
Graph.graphData(DATA);
rafOn=true; requestAnimationFrame(fadeFrame);   // 预布局期间全部 opacity 0（隐藏）
Graph.onEngineStop(function(){
  if(pinned) return; pinned=true;
  DATA.nodes.forEach(function(n){ n.fx=n.x; n.fy=n.y; n.fz=n.z; });  // 钉住，生长期间不再受力扰动
  Graph.zoomToFit(0, 70);
  beginReveal();
});

// ── 学习路径导览 ──
var pnbody=document.getElementById('pnbody');
PATH.forEach(function(u){
  var ud=document.createElement('div'); ud.className='unit';
  var uh=document.createElement('div'); uh.className='uhead'; uh.textContent=u.name;
  uh.onclick=function(){
    var ids=DATA.nodes.filter(function(n){return (n.units||[]).indexOf(u.unitId)>=0;}).map(function(n){return n.id;});
    if(ids.length) focusSet(ids, u.name+'（整单元 '+ids.length+' 节点）');
  };
  ud.appendChild(uh);
  u.lessons.forEach(function(l){
    var ld=document.createElement('div'); ld.className='lesson'; ld.textContent='· '+l.title;
    ld.onclick=function(){
      var ids=DATA.nodes.filter(function(n){return (n.lessons||[]).indexOf(l.lessonId)>=0;}).map(function(n){return n.id;});
      if(ids.length) focusSet(ids, u.name+' / '+l.title);
    };
    ud.appendChild(ld);
  });
  pnbody.appendChild(ud);
});
document.getElementById('pnhead').onclick=function(){
  var b=document.getElementById('pnbody'); b.style.display=(b.style.display==='none')?'block':'none';
};

// ── 图例 ──
var lg=document.getElementById('legend');
DATA.categories.forEach(function(c,i){
  var d=document.createElement('div'); d.className='item';
  d.innerHTML='<span class="dot" style="background:'+palette[i%palette.length]+'"></span>'+esc(c); lg.appendChild(d);
});

// ── 控件 ──
document.getElementById('btnReplay').onclick=function(){ if(pinned) beginReveal(); };
document.getElementById('q').addEventListener('keydown',function(e){
  if(e.key!=='Enter') return; var q=e.target.value.trim(); if(!q) return;
  finishGrowthNow();
  var hit=DATA.nodes.find(function(n){return n.name.indexOf(q)>=0;});
  if(hit){ focusNode(hit); updateFocus(hit); showDetail(hit); }
});
document.getElementById('reset').addEventListener('click',function(){ updateFocus(null); document.getElementById('q').value=''; });
window.addEventListener('resize',function(){ Graph.width(window.innerWidth).height(window.innerHeight); });
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--title", default="知识图谱 3D")
    ap.add_argument("--path-json", default="", help="学习路径 JSON（[{unitId,name,lessons:[{lessonId,title}]}]）")
    args = ap.parse_args()
    graph = json.loads(Path(args.graph).read_text(encoding="utf-8"))
    p = build_payload(graph)
    path_data = []
    if args.path_json and Path(args.path_json).exists():
        path_data = json.loads(Path(args.path_json).read_text(encoding="utf-8"))
    out = (HTML_TMPL
           .replace("__TITLE__", html.escape(args.title))
           .replace("__N_ENT__", str(p["stats"]["entities"]))
           .replace("__N_REL__", str(p["stats"]["relations"]))
           .replace("__N_CAT__", str(p["stats"]["categories"]))
           .replace("__PATH__", json.dumps(path_data, ensure_ascii=False))
           .replace("__DATA__", json.dumps(p, ensure_ascii=False)))
    Path(args.output).write_text(out, encoding="utf-8")
    print(f"ok: {p['stats']['entities']} nodes, {p['stats']['relations']} edges, "
          f"{len(path_data)} units -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
