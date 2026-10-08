#!/usr/bin/env python3
"""来源元数据提取（确定性，无模型参与）。

从源文件推断结构化归属信息（unitId / lessonId / textbookId 等），供两处复用：
- normalize_graph.py：把归属信息回填进实体 attributes（渲染层下钻靠它）
- build_learning_path.py：生成 learning_path.json 导览数据

提取顺序（先命中者优先，不覆盖已有键）：
1. YAML frontmatter（文件头 --- ... --- 之间的 key: value）
2. 文件名模式 <textbookId>_<unitId>_<lessonId>.md（纯数字段）

只认白名单键，避免把无关 frontmatter（title/author/date…）灌进图谱。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# 参与图谱下钻 / 导览联动的归属键（白名单，顺序即展示优先级）
DEFAULT_META_KEYS = ("textbookId", "unitId", "lessonId")

_FRONTMATTER_RE = re.compile(r"\A\ufeff?\s*---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)
_KV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$")
# <textbookId>_<unitId>_<lessonId>，各段为纯数字
_FILENAME_RE = re.compile(r"^(\d+)_(\d+)_(\d+)$")


def parse_frontmatter(text: str) -> dict[str, str]:
    """解析文件头 YAML frontmatter 的平坦 key: value（不支持嵌套/列表，够用即可）。"""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        kv = _KV_RE.match(line)
        if not kv:
            continue
        key, value = kv.group(1), kv.group(2).strip()
        # 去掉包裹引号
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()
        if value:
            meta[key] = value
    return meta


def meta_from_filename(path: str | Path, keys: tuple[str, ...] = DEFAULT_META_KEYS) -> dict[str, str]:
    """从 <textbookId>_<unitId>_<lessonId>.md 形式的文件名提取归属 ID。"""
    stem = Path(str(path)).stem
    # 分块名 file_id#partN → 取 # 前
    stem = stem.split("#", 1)[0]
    match = _FILENAME_RE.match(stem)
    if not match:
        return {}
    ordered = ("textbookId", "unitId", "lessonId")
    found = dict(zip(ordered, match.groups()))
    return {k: v for k, v in found.items() if k in keys}


def extract_source_meta(
    source: str,
    source_root: str | Path | None = None,
    keys: tuple[str, ...] = DEFAULT_META_KEYS,
) -> dict[str, str]:
    """综合 frontmatter + 文件名，返回 source 对应的归属元数据（只含白名单键）。

    source: raws.jsonl 里的来源标识（通常是文件相对路径，可带 #partN 后缀）
    source_root: 相对路径解析基准目录；None 表示按当前工作目录解析
    """
    if not source:
        return {}
    # 去掉分块后缀，定位真实文件
    file_part = str(source).split("#", 1)[0]
    candidate = Path(file_part)
    if source_root and not candidate.is_absolute():
        candidate = Path(source_root) / candidate

    meta: dict[str, str] = {}
    if candidate.is_file():
        try:
            head = candidate.read_text(encoding="utf-8", errors="replace")[:4096]
        except OSError:
            head = ""
        for key, value in parse_frontmatter(head).items():
            if key in keys:
                meta[key] = value

    # 文件名兜底：不覆盖 frontmatter 已给出的键
    for key, value in meta_from_filename(file_part, keys).items():
        meta.setdefault(key, value)
    return meta


def meta_to_attributes(meta: dict[str, str], keys: tuple[str, ...] = DEFAULT_META_KEYS) -> list[dict[str, str]]:
    """归属元数据 → 实体 attributes 数组（{text, label} 结构）。"""
    attributes: list[dict[str, Any]] = []
    for key in keys:
        value = str(meta.get(key) or "").strip()
        if value:
            attributes.append({"text": value, "label": key})
    return attributes


def resolve_path_data(path_json: str, graph_path: str) -> list:
    """学习路径数据解析（渲染脚本共用）。

    显式 --path-json 优先；缺省自动探测 graph 同目录的 learning_path.json，
    使导览面板成为默认行为而非必须手传的可选项。
    """
    import json as _json

    candidates = []
    if path_json:
        candidates.append(Path(path_json))
    else:
        candidates.append(Path(graph_path).resolve().parent / "learning_path.json")
    for candidate in candidates:
        if candidate.is_file():
            try:
                data = _json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, list):
                return data
    return []
