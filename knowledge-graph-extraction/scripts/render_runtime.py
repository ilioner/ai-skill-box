"""生成相对于 HTML 的外部图谱及学习路径 URL。"""

import json
import os
from pathlib import Path
from urllib.parse import quote


def build_runtime_boot(template: str, graph_path: str, output_path: str, path_json: str) -> str:
    graph = Path(graph_path).resolve()
    learning_path = Path(path_json).resolve() if path_json else graph.with_name("learning_path.json")
    output_dir = Path(output_path).resolve().parent
    for placeholder, data_path in [("__GRAPH_URL__", graph), ("__PATH_URL__", learning_path)]:
        relative = Path(os.path.relpath(data_path, output_dir)).as_posix()
        template = template.replace(placeholder, json.dumps(quote(relative, safe="/")))
    return template.replace("__PATH_REQUIRED__", "true" if path_json else "false")
