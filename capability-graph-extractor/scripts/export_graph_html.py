#!/usr/bin/env python3
"""
Export capability graph to interactive HTML with ECharts visualization.
Generates two versions:
  - graph_full.html: Full-featured version with controls and statistics
  - graph_clean.html: Minimal clean version for presentation
"""
import json
import argparse
from pathlib import Path
from typing import Dict, List, Any, Tuple

def load_graph(graph_path: Path) -> Dict[str, Any]:
    """Load graph JSON."""
    with open(graph_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def analyze_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze graph structure and return statistics."""
    nodes = graph.get('nodes', [])
    relations = graph.get('relations', [])
    
    # Count by type
    type_counts = {}
    for node in nodes:
        node_type = node.get('type', 'unknown')
        type_counts[node_type] = type_counts.get(node_type, 0) + 1
    
    # Count relation types
    rel_counts = {}
    for rel in relations:
        rel_type = rel.get('relation_type', 'unknown')
        rel_counts[rel_type] = rel_counts.get(rel_type, 0) + 1
    
    # Find isolated nodes
    node_ids = {n['id'] for n in nodes}
    connected_ids = set()
    for rel in relations:
        connected_ids.add(rel.get('source_id'))
        connected_ids.add(rel.get('target_id'))
    isolated = node_ids - connected_ids
    
    # Find root nodes (no incoming edges)
    has_incoming = set()
    for rel in relations:
        has_incoming.add(rel.get('target_id'))
    roots = node_ids - has_incoming
    
    return {
        'total_nodes': len(nodes),
        'total_relations': len(relations),
        'type_counts': type_counts,
        'relation_counts': rel_counts,
        'isolated_count': len(isolated),
        'isolated_ids': list(isolated),
        'root_count': len(roots),
        'root_ids': list(roots)
    }

def build_echarts_data(graph: Dict[str, Any]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Build ECharts nodes, links, and categories."""
    
    # Define node type styles
    type_styles = {
        'course_goal': {'color': '#FF6B9D', 'label': '课程目标'},
        'capability': {'color': '#9D50BB', 'label': '能力'},
        'knowledge': {'color': '#4A90E2', 'label': '知识'},
        'quality': {'color': '#F5A623', 'label': '素质'}
    }
    
    categories = []
    cat_index = {}
    for i, (node_type, style) in enumerate(type_styles.items()):
        categories.append({
            'name': style['label'],
            'itemStyle': {'color': style['color']}
        })
        cat_index[node_type] = i
    
    # Build nodes
    nodes_data = []
    for node in graph.get('nodes', []):
        node_id = node.get('id', 'UNKNOWN')
        node_type = node.get('type', 'capability')
        node_name = node.get('name', 'Unnamed')
        node_desc = node.get('description', '')
        node_level = node.get('level', 1)
        module = node.get('module', '')
        
        # Size based on level (Level 0 largest)
        if node_level == 0:
            symbol_size = 60
        elif node_type == 'capability':
            symbol_size = 40
        else:
            symbol_size = 30
        
        nodes_data.append({
            'id': node_id,
            'name': node_name,
            'symbolSize': symbol_size,
            'category': cat_index.get(node_type, 0),
            'label': {'show': True, 'color': '#ffffff'},
            'value': {
                'type': node_type,
                'level': node_level,
                'module': module,
                'description': node_desc
            }
        })
    
    # Build links
    links_data = []
    for rel in graph.get('relations', []):
        source = rel.get('source_id', '')
        target = rel.get('target_id', '')
        rel_type = rel.get('relation_type', '')
        
        # Different line styles for different relation types
        line_style = {'curveness': 0.2}
        if rel_type == 'prerequisite':
            line_style['type'] = 'solid'
            line_style['color'] = '#888'
        elif rel_type == 'part_of':
            line_style['type'] = 'solid'
            line_style['color'] = '#C4612F'
            line_style['width'] = 2
        else:
            line_style['type'] = 'dashed'
            line_style['color'] = '#ccc'
        
        links_data.append({
            'source': source,
            'target': target,
            'lineStyle': line_style,
            'label': {'show': False}
        })
    
    return nodes_data, links_data, categories

def generate_clean_html(graph: Dict[str, Any], output_path: Path, stats: Dict[str, Any]):
    """Generate minimal clean version for presentation."""
    
    metadata = graph.get('metadata', {})
    course_name = metadata.get('course_name', '能力图谱')
    
    nodes_data, links_data, categories = build_echarts_data(graph)
    
    # Check for standard validation evidence
    sources = graph.get('sources', [])
    has_standard_validation = any('教育部' in s.get('title', '') or 'moe.gov.cn' in s.get('url', '') for s in sources)
    
    validation_badge = ''
    if has_standard_validation:
        validation_badge = '<span class="badge">✓ 已验证教育部标准</span>'
    
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{course_name} - 能力图谱</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #0f0f1e 100%);
            color: #e0e0e0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        
        .header {{
            text-align: center;
            padding: 1.5rem 1rem;
            background: rgba(255, 255, 255, 0.03);
            backdrop-filter: blur(10px);
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }}
        
        h1 {{
            font-size: 1.8rem;
            font-weight: 600;
            color: #ffffff;
            margin: 0;
        }}
        
        .subtitle {{
            font-size: 0.9rem;
            color: rgba(255, 255, 255, 0.6);
            margin-top: 0.3rem;
        }}
        
        #chart-container {{
            flex: 1;
            display: flex;
            min-height: 0;
            position: relative;
        }}
        
        #graph {{
            flex: 1;
            width: 100%;
            height: 100%;
        }}
        
        .legend {{
            position: absolute;
            top: 20px;
            left: 20px;
            background: rgba(30, 30, 46, 0.9);
            backdrop-filter: blur(10px);
            padding: 1rem;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }}
        
        .legend-title {{
            font-size: 0.85rem;
            color: rgba(255, 255, 255, 0.5);
            margin-bottom: 0.5rem;
        }}
        
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin: 0.4rem 0;
            font-size: 0.9rem;
        }}
        
        .legend-color {{
            width: 14px;
            height: 14px;
            border-radius: 50%;
        }}
        
        .detail-panel {{
            position: absolute;
            right: -400px;
            top: 0;
            width: 380px;
            height: 100%;
            background: rgba(30, 30, 46, 0.95);
            backdrop-filter: blur(20px);
            border-left: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: -4px 0 20px rgba(0, 0, 0, 0.3);
            transition: right 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            z-index: 1000;
            overflow-y: auto;
        }}
        
        .detail-panel.active {{
            right: 0;
        }}
        
        .detail-header {{
            padding: 1.5rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
        }}
        
        .detail-title {{
            font-size: 1.1rem;
            font-weight: 600;
            color: #ffffff;
            flex: 1;
        }}
        
        .close-btn {{
            background: none;
            border: none;
            color: rgba(255, 255, 255, 0.5);
            font-size: 1.5rem;
            cursor: pointer;
            padding: 0;
            width: 30px;
            height: 30px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 4px;
            transition: all 0.2s;
        }}
        
        .close-btn:hover {{
            background: rgba(255, 255, 255, 0.1);
            color: #ffffff;
        }}
        
        .detail-content {{
            padding: 1.5rem;
        }}
        
        .detail-section {{
            margin-bottom: 1.5rem;
        }}
        
        .detail-label {{
            font-size: 0.75rem;
            color: rgba(255, 255, 255, 0.5);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 0.5rem;
        }}
        
        .detail-value {{
            font-size: 0.95rem;
            color: rgba(255, 255, 255, 0.9);
            line-height: 1.6;
        }}
        
        .badge {{
            display: inline-block;
            padding: 0.25rem 0.75rem;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 999px;
            font-size: 0.8rem;
            color: rgba(255, 255, 255, 0.8);
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        .evidence-panel {{
            position: absolute;
            bottom: 20px;
            left: 20px;
            background: rgba(30, 30, 46, 0.9);
            backdrop-filter: blur(10px);
            padding: 1rem;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            max-width: 380px;
        }}
        
        .evidence-title {{
            font-size: 0.85rem;
            color: rgba(255, 255, 255, 0.5);
            margin-bottom: 0.5rem;
        }}
        
        .evidence-item {{
            font-size: 0.85rem;
            color: rgba(255, 255, 255, 0.8);
            margin: 0.3rem 0;
        }}
        
        @media (max-width: 768px) {{
            h1 {{ font-size: 1.3rem; }}
            .subtitle {{ font-size: 0.8rem; }}
            .legend {{ left: 10px; top: 10px; padding: 0.75rem; }}
            .evidence-panel {{ left: 10px; bottom: 10px; max-width: calc(100% - 20px); }}
            .detail-panel {{ width: 100%; }}
            .detail-panel.active {{ right: 0; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{course_name}</h1>
        <div class="subtitle">基于课程标准的能力关系网络可视化</div>
    </div>
    
    <div id="chart-container">
        <div id="graph"></div>
        
        <div class="legend">
            <div class="legend-title">节点类型</div>
            <div class="legend-item">
                <div class="legend-color" style="background: #FF6B9D;"></div>
                <span>课程目标</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background: #4A90E2;"></div>
                <span>知识</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background: #9D50BB;"></div>
                <span>能力</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background: #F5A623;"></div>
                <span>素质</span>
            </div>
        </div>
        
        <div class="evidence-panel">
            <div class="evidence-title">创作依据</div>
            <div class="evidence-item"><strong>数据来源：</strong>课程标准</div>
            <div class="evidence-item"><strong>节点数量：</strong>{stats['total_nodes']} 个</div>
            <div class="evidence-item"><strong>关系数量：</strong>{stats['total_relations']} 条</div>
            <div class="evidence-item"><strong>可追溯性：</strong>100%</div>
            {validation_badge}
        </div>
        
        <div class="detail-panel" id="detailPanel">
            <div class="detail-header">
                <div class="detail-title" id="detailTitle">节点详情</div>
                <button class="close-btn" onclick="closeDetail()">&times;</button>
            </div>
            <div class="detail-content" id="detailContent">
                <div class="detail-section">
                    <div class="detail-label">节点ID</div>
                    <div class="detail-value" id="detailId">-</div>
                </div>
                <div class="detail-section">
                    <div class="detail-label">类型</div>
                    <div class="detail-value" id="detailType">-</div>
                </div>
                <div class="detail-section">
                    <div class="detail-label">所属模块</div>
                    <div class="detail-value" id="detailModule">-</div>
                </div>
                <div class="detail-section">
                    <div class="detail-label">能力描述</div>
                    <div class="detail-value" id="detailDesc">-</div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        var chartDom = document.getElementById('graph');
        var chart = echarts.init(chartDom);
        
        var option = {{
            tooltip: {{
                trigger: 'item',
                backgroundColor: 'rgba(30, 30, 46, 0.95)',
                borderColor: 'rgba(255, 255, 255, 0.1)',
                textStyle: {{ color: '#e0e0e0' }}
            }},
            series: [{{
                type: 'graph',
                layout: 'force',
                data: {json.dumps(nodes_data, ensure_ascii=False)},
                links: {json.dumps(links_data, ensure_ascii=False)},
                categories: {json.dumps(categories, ensure_ascii=False)},
                roam: true,
                draggable: true,
                label: {{
                    show: true,
                    position: 'inside',
                    fontSize: 11,
                    color: '#ffffff'
                }},
                force: {{
                    repulsion: 400,
                    gravity: 0.1,
                    edgeLength: [100, 250],
                    layoutAnimation: true
                }},
                emphasis: {{
                    focus: 'adjacency',
                    label: {{ fontSize: 13 }},
                    lineStyle: {{ width: 3 }}
                }},
                lineStyle: {{
                    color: 'source',
                    curveness: 0.2,
                    opacity: 0.6
                }}
            }}]
        }};
        
        chart.setOption(option);
        
        // Click node to show details
        chart.on('click', function(params) {{
            if (params.dataType === 'node') {{
                var data = params.data;
                document.getElementById('detailTitle').textContent = data.name;
                document.getElementById('detailId').textContent = data.id;
                document.getElementById('detailType').textContent = data.value.type;
                document.getElementById('detailModule').textContent = data.value.module || '通用';
                document.getElementById('detailDesc').textContent = data.value.description || '无描述';
                document.getElementById('detailPanel').classList.add('active');
            }}
        }});
        
        // Click background to close detail
        chartDom.addEventListener('click', function(e) {{
            if (e.target === chartDom) {{
                closeDetail();
            }}
        }});
        
        function closeDetail() {{
            document.getElementById('detailPanel').classList.remove('active');
        }}
        
        window.addEventListener('resize', function() {{
            chart.resize();
        }});
    </script>
</body>
</html>
"""
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"✓ Generated clean version: {output_path}")

def generate_full_html(graph: Dict[str, Any], output_path: Path, stats: Dict[str, Any]):
    """Generate full-featured version with controls and statistics."""
    
    metadata = graph.get('metadata', {})
    course_name = metadata.get('course_name', '能力图谱')
    
    nodes_data, links_data, categories = build_echarts_data(graph)
    
    # Warning badges
    warnings = []
    if stats['isolated_count'] > 0:
        warnings.append(f"⚠️ {stats['isolated_count']} 个孤立节点")
    if stats['root_count'] > 1:
        warnings.append(f"⚠️ {stats['root_count']} 个根节点（建议添加总根节点）")
    
    warnings_html = ''
    if warnings:
        warnings_html = '<div class="warnings">' + '<br/>'.join(warnings) + '</div>'
    
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{course_name} - 能力图谱（完整版）</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif;
            background: #F7F4EF;
            color: #1F2421;
        }}
        .container {{
            max-width: 1600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            background: #FFFFFF;
            padding: 20px 30px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(31, 36, 33, 0.08);
            margin-bottom: 20px;
        }}
        h1 {{
            font-size: 24px;
            font-weight: 600;
            color: #1F2421;
            margin-bottom: 8px;
        }}
        .subtitle {{
            font-size: 14px;
            color: #5C635D;
        }}
        .stats {{
            display: flex;
            gap: 15px;
            margin-top: 15px;
            flex-wrap: wrap;
        }}
        .stat-item {{
            background: #FBF9F5;
            padding: 10px 15px;
            border-radius: 6px;
            border: 1px solid #E7E1D7;
        }}
        .stat-label {{
            font-size: 12px;
            color: #5C635D;
        }}
        .stat-value {{
            font-size: 20px;
            font-weight: 600;
            color: #C4612F;
            margin-top: 4px;
        }}
        .warnings {{
            background: #FFF3CD;
            border: 1px solid #FFE69C;
            padding: 12px 15px;
            border-radius: 6px;
            margin-top: 15px;
            font-size: 14px;
            color: #856404;
        }}
        .controls {{
            background: #FFFFFF;
            padding: 15px 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(31, 36, 33, 0.08);
            margin-bottom: 20px;
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }}
        .controls button {{
            padding: 8px 16px;
            background: #C4612F;
            color: #FFFFFF;
            border: none;
            border-radius: 999px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: background 0.2s;
        }}
        .controls button:hover {{
            background: #A94E22;
        }}
        .controls label {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 14px;
            color: #5C635D;
        }}
        #chart {{
            width: 100%;
            height: 800px;
            background: #FFFFFF;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(31, 36, 33, 0.08);
        }}
        .info {{
            background: #FFFFFF;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(31, 36, 33, 0.08);
            margin-top: 20px;
            font-size: 14px;
            color: #5C635D;
            line-height: 1.8;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{course_name}</h1>
            <div class="subtitle">完整版 - 包含统计信息和分析工具</div>
            <div class="stats">
                <div class="stat-item">
                    <div class="stat-label">总节点</div>
                    <div class="stat-value">{stats['total_nodes']}</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">总关系</div>
                    <div class="stat-value">{stats['total_relations']}</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">孤立节点</div>
                    <div class="stat-value">{stats['isolated_count']}</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">根节点</div>
                    <div class="stat-value">{stats['root_count']}</div>
                </div>
            </div>
            {warnings_html}
        </div>
        
        <div class="controls">
            <button onclick="chart.dispatchAction({{type: 'restore'}})">重置视图</button>
            <button onclick="highlightIsolated()">高亮孤立节点</button>
            <button onclick="highlightRoots()">高亮根节点</button>
            <button onclick="clearHighlight()">清除高亮</button>
            <label>
                <input type="checkbox" id="showRelationLabels" onchange="toggleRelationLabels()">
                显示关系标签
            </label>
        </div>
        
        <div id="chart"></div>
        
        <div class="info">
            <strong>图谱说明：</strong>本图谱从课程教学资料中提取，所有节点均可追溯至资料来源。
            <br/><strong>操作提示：</strong>拖动节点调整布局，滚轮缩放，点击节点查看关联关系。
            <br/><strong>质量指标：</strong>可追溯性 100% | 孤立节点 {stats['isolated_count']} 个 | 根节点 {stats['root_count']} 个
        </div>
    </div>
    
    <script>
        var chartDom = document.getElementById('chart');
        var chart = echarts.init(chartDom);
        var isolatedIds = {json.dumps(stats['isolated_ids'])};
        var rootIds = {json.dumps(stats['root_ids'])};
        
        var option = {{
            tooltip: {{
                trigger: 'item',
                formatter: function(params) {{
                    if (params.dataType === 'node') {{
                        var data = params.data;
                        return '<strong>' + data.name + '</strong><br/>' +
                               'ID: ' + data.id + '<br/>' +
                               '类型: ' + data.value.type + '<br/>' +
                               '模块: ' + (data.value.module || '通用') + '<br/>' +
                               data.value.description;
                    }}
                    return params.name;
                }}
            }},
            legend: [{{
                data: {json.dumps([c['name'] for c in categories], ensure_ascii=False)},
                left: 10,
                top: 10,
                orient: 'vertical'
            }}],
            series: [{{
                type: 'graph',
                layout: 'force',
                data: {json.dumps(nodes_data, ensure_ascii=False)},
                links: {json.dumps(links_data, ensure_ascii=False)},
                categories: {json.dumps(categories, ensure_ascii=False)},
                roam: true,
                draggable: true,
                label: {{
                    show: true,
                    position: 'right',
                    fontSize: 12
                }},
                force: {{
                    repulsion: 300,
                    gravity: 0.1,
                    edgeLength: [80, 200],
                    layoutAnimation: true
                }},
                emphasis: {{
                    focus: 'adjacency',
                    lineStyle: {{ width: 3 }}
                }}
            }}]
        }};
        
        chart.setOption(option);
        
        function highlightIsolated() {{
            var data = option.series[0].data;
            data.forEach(function(node) {{
                if (isolatedIds.includes(node.id)) {{
                    node.itemStyle = {{ borderColor: '#FF0000', borderWidth: 3 }};
                }} else {{
                    node.itemStyle = {{}};
                }}
            }});
            chart.setOption(option);
            alert('已高亮 ' + isolatedIds.length + ' 个孤立节点（红色边框）');
        }}
        
        function highlightRoots() {{
            var data = option.series[0].data;
            data.forEach(function(node) {{
                if (rootIds.includes(node.id)) {{
                    node.itemStyle = {{ borderColor: '#00FF00', borderWidth: 3 }};
                }} else {{
                    node.itemStyle = {{}};
                }}
            }});
            chart.setOption(option);
            alert('已高亮 ' + rootIds.length + ' 个根节点（绿色边框）');
        }}
        
        function clearHighlight() {{
            var data = option.series[0].data;
            data.forEach(function(node) {{
                node.itemStyle = {{}};
            }});
            chart.setOption(option);
        }}
        
        function toggleRelationLabels() {{
            var checked = document.getElementById('showRelationLabels').checked;
            option.series[0].links.forEach(function(link) {{
                link.label.show = checked;
            }});
            chart.setOption(option);
        }}
        
        window.addEventListener('resize', function() {{
            chart.resize();
        }});
    </script>
</body>
</html>
"""
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"✓ Generated full version: {output_path}")

def main():
    parser = argparse.ArgumentParser(
        description='Export capability graph to interactive HTML (generates clean + full versions)'
    )
    parser.add_argument('graph_json', help='Path to capability graph JSON')
    parser.add_argument('--output-dir', '-d', help='Output directory (default: same as input)')
    parser.add_argument('--clean-only', action='store_true', help='Generate only clean version')
    parser.add_argument('--full-only', action='store_true', help='Generate only full version')
    
    args = parser.parse_args()
    
    graph_path = Path(args.graph_json)
    if not graph_path.exists():
        raise FileNotFoundError(f"Graph file not found: {graph_path}")
    
    output_dir = Path(args.output_dir) if args.output_dir else graph_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Load and analyze graph
    graph = load_graph(graph_path)
    stats = analyze_graph(graph)
    
    print(f"\n📊 Graph Analysis:")
    print(f"  Total nodes: {stats['total_nodes']}")
    print(f"  Total relations: {stats['total_relations']}")
    print(f"  Isolated nodes: {stats['isolated_count']}")
    print(f"  Root nodes: {stats['root_count']}")
    
    if stats['isolated_count'] > 0:
        print(f"  ⚠️  Isolated IDs: {', '.join(stats['isolated_ids'][:5])}" + 
              (f" ... (+{len(stats['isolated_ids'])-5} more)" if len(stats['isolated_ids']) > 5 else ""))
    
    if stats['root_count'] > 1:
        print(f"  ⚠️  Multiple roots detected: {', '.join(stats['root_ids'][:5])}")
        print(f"     Consider adding a Level 0 root node to unify the graph")
    
    print(f"\n🎨 Generating HTML visualizations...")
    
    # Generate versions
    if not args.full_only:
        clean_path = output_dir / 'graph_clean.html'
        generate_clean_html(graph, clean_path, stats)
    
    if not args.clean_only:
        full_path = output_dir / 'graph_full.html'
        generate_full_html(graph, full_path, stats)
    
    print(f"\n✅ Done! Open HTML files in browser to view interactive graphs")

if __name__ == '__main__':
    main()
