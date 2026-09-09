#!/usr/bin/env python3
"""生成 / 增量维护知识导图 JSON 树。

工作流：
1. 读取文件清单（目录扫描 / JSON / JSONL）
2. 全量：LLM 把文件清单组织成 2-4 层树（根=库名，一级=分类，叶子=文件名）
3. 增量：diff 检测 → 删除走纯树手术（无 LLM）→ 新增调 LLM 整合进现有结构
4. 校验（每个文件名只出现一次、叶子 children 为空），输出树 + 追踪映射

用法：
  # 全量生成
  python3 generate_mindmap.py --mode full --files-dir docs/ --output mindmap.json \
      --model gpt-4o-mini --api-key $OPENAI_API_KEY --root-name "产品资料库" --apply
  python3 generate_mindmap.py --mode full --files-json files.json --output mindmap.json --root-name "知识库" --apply

  # 增量更新（门禁：--mode 必填；先看计划，确认后再 --apply）
  python3 generate_mindmap.py --mode incremental --existing mindmap.json --tracked tracked.json \
      --files-json files.json --output mindmap.json
  python3 generate_mindmap.py --mode incremental --existing mindmap.json --tracked tracked.json \
      --files-json files.json --output mindmap.json --api-key $OPENAI_API_KEY --apply

环境变量：OPENAI_API_KEY / OPENAI_BASE_URL / MINDMAP_MODEL。API 为 OpenAI 兼容 /chat/completions。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mindmap_tree import (  # noqa: E402
    collect_leaf_filenames,
    detect_changes,
    load_files,
    remove_files,
    validate,
)

DEFAULT_MAX_FILES = 200

MINDMAP_SYSTEM_PROMPT = """你是一个专业的知识整理助手。

你的任务是分析用户提供的文件列表，生成一个层次分明的思维导图结构。

**核心规则：每个文件名只能出现一次！不允许重复！**

要求：
1. 思维导图要有清晰的层级结构（2-4层）
2. 根节点是知识库名称
3. 第一层是主要分类（如：技术文档、规章制度、数据资源等）
4. 第二层是子分类
5. **叶子节点必须是具体的文件名称**
6. **每个文件名在整个思维导图中只能出现一次，不得重复！**
7. 如果一个文件可能属于多个分类，只选择最合适的一个分类放置
8. 使用合适的emoji图标增强可读性
9. 返回JSON格式，遵循以下结构：

```json
{
  "content": "知识库名称",
  "children": [
    {
      "content": "🎯 主分类1",
      "children": [
        {
          "content": "子分类1.1",
          "children": [
            {"content": "文件名1.txt", "children": []},
            {"content": "文件名2.pdf", "children": []}
          ]
        }
      ]
    },
    {
      "content": "💻 主分类2",
      "children": [
        {"content": "文件名3.docx", "children": []},
        {"content": "文件名4.md", "children": []}
      ]
    }
  ]
}
```

**重要约束：**
- 每个文件名在整个JSON中只能出现一次
- 不要按多个维度分类导致文件重复
- 选择最主要、最合适的分类维度
- 每个叶子节点的children必须是空数组[]
- 分类名称要简洁明了
- 使用emoji增强视觉效果
"""

MINDMAP_INCREMENTAL_SYSTEM_PROMPT = """你是一个专业的知识整理助手。

你的任务是将新文件整合到已有的思维导图结构中。

**核心规则：**
1. 保留现有思维导图的分类结构不变
2. 将新文件添加到最合适的已有分类下
3. 如果新文件不属于任何现有分类，可以创建新的分类节点
4. 每个文件名只能出现一次，不允许重复
5. 如果已有分类名称需要微调以容纳新文件，可以适当调整
6. 返回完整的思维导图JSON（包含原有结构 + 新文件）

返回JSON格式同标准思维导图结构。
"""


# ─── 输入 ───────────────────────────────────────────────────


def scan_directory(path: str, limit: int) -> list[dict[str, str]]:
    files = []
    for fp in sorted(Path(path).rglob("*")):
        if not fp.is_file():
            continue
        relative = str(fp.relative_to(path))
        if relative.startswith("."):
            continue
        files.append({"file_id": relative, "filename": fp.name, "type": fp.suffix.lstrip(".")})
        if len(files) >= limit:
            break
    return files


# ─── LLM ────────────────────────────────────────────────────


def chat_completion(messages: list[dict[str, str]], api_base: str, api_key: str, model: str,
                    temperature: float = 0.2, timeout: int = 120) -> str:
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": temperature, "stream": False}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"模型响应格式异常: {exc}") from exc


def parse_mindmap_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出中没有 JSON 对象")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict) or "content" not in data or "children" not in data:
        raise ValueError("导图结构不正确：缺少 content 或 children")
    return data


def build_user_message(root_name: str, files_info: list[dict[str, str]], user_prompt: str = "") -> str:
    files_text = "\n".join([f"- {f['filename']} ({f['type']})" for f in files_info])
    lines = [
        f'请为知识库"{root_name}"生成思维导图结构。',
        f"文件列表（共{len(files_info)}个文件）：",
        files_text,
        f"用户补充说明：{user_prompt}" if user_prompt else "",
        "**重要提醒：**",
        f"1. 这个知识库共有{len(files_info)}个文件",
        "2. 每个文件名只能在思维导图中出现一次",
        "3. 不要让同一个文件出现在多个分类下",
        "4. 为每个文件选择最合适的唯一分类",
        "请生成合理的思维导图结构。",
    ]
    return "\n".join(line for line in lines if line)


def build_incremental_user_message(root_name: str, mindmap: dict[str, Any],
                                   added_files: list[dict[str, str]], user_prompt: str = "") -> str:
    existing = json.dumps(mindmap, ensure_ascii=False, indent=2)
    files_text = "\n".join([f"- {f['filename']} ({f['type']})" for f in added_files])
    lines = [
        f'请将以下新文件整合到知识库"{root_name}"的现有思维导图中。',
        "现有思维导图结构：",
        existing,
        f"新增文件列表（共{len(added_files)}个文件）：",
        files_text,
        f"用户补充说明：{user_prompt}" if user_prompt else "",
        "**重要提醒：**",
        "1. 保留现有分类结构，将新文件添加到最合适的已有分类下",
        "2. 如果新文件不适合任何现有分类，创建新的分类节点",
        "3. 每个文件名只能出现一次",
        "4. 返回完整的思维导图JSON（包含原有结构 + 新文件）",
        "请整合新文件到现有结构中。",
    ]
    return "\n".join(line for line in lines if line)


# ─── 生成 ───────────────────────────────────────────────────


def generate_full(files_info: list[dict[str, str]], root_name: str, user_prompt: str,
                  api_base: str, api_key: str, model: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": MINDMAP_SYSTEM_PROMPT},
        {"role": "user", "content": build_user_message(root_name, files_info, user_prompt)},
    ]
    response = chat_completion(messages, api_base, api_key, model)
    return parse_mindmap_content(response)


def update_incremental(mindmap: dict[str, Any], added_files: list[dict[str, str]], root_name: str,
                       user_prompt: str, api_base: str, api_key: str, model: str) -> dict[str, Any]:
    if not added_files:
        return mindmap
    messages = [
        {"role": "system", "content": MINDMAP_INCREMENTAL_SYSTEM_PROMPT},
        {"role": "user", "content": build_incremental_user_message(root_name, mindmap, added_files, user_prompt)},
    ]
    response = chat_completion(messages, api_base, api_key, model)
    return parse_mindmap_content(response)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 / 增量维护知识导图 JSON 树")
    parser.add_argument("--files-dir", help="扫描目录下的文件名（递归）")
    parser.add_argument("--files-json", help="文件清单 JSON/JSONL：[{id|file_id, filename, type}]")
    parser.add_argument("--tree-json", help="Agent 直抽模式：由 Agent 生成的导图树 JSON（跳过 LLM 调用）")
    parser.add_argument("--mode", required=True, choices=["full", "incremental"],
                        help="full=全量重建；incremental=增量更新。运行前必须先与用户确认模式")
    parser.add_argument("--existing", help="现有导图 JSON（incremental 必需）")
    parser.add_argument("--tracked", help="追踪文件 JSON：{file_id: filename}（incremental）")
    parser.add_argument("--output", default="mindmap.json", help="输出导图 JSON（默认 mindmap.json）")
    parser.add_argument("--tracked-output", default="tracked.json", help="输出追踪映射（默认 tracked.json）")
    parser.add_argument("--root-name", default="知识库", help="根节点名称")
    parser.add_argument("--user-prompt", default="", help="用户补充说明")
    parser.add_argument("--model", default=os.getenv("MINDMAP_MODEL") or "gpt-4o-mini")
    parser.add_argument("--api-base", default=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1")
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help=f"文件数上限（默认 {DEFAULT_MAX_FILES}）")
    parser.add_argument("--apply", action="store_true", help="执行并写文件；不加则只打印计划")
    args = parser.parse_args()

    # 读取文件清单
    if args.files_dir:
        files_info = scan_directory(args.files_dir, args.max_files)
    elif args.files_json:
        files_info = load_files(args.files_json)
        if len(files_info) > args.max_files:
            files_info = files_info[: args.max_files]
    else:
        print("error: 需要 --files-dir 或 --files-json", file=sys.stderr)
        return 1
    if not files_info:
        print("error: 文件清单为空", file=sys.stderr)
        return 1
    current_files = {f["file_id"] or f["filename"]: f for f in files_info}

    # ── 门禁：模式由 --mode 强制；incremental 必须提供现有导图 ──
    if args.mode == "incremental" and not args.existing:
        print("error: 增量模式需要 --existing（现有导图 JSON）。首次生成请用 --mode full。", file=sys.stderr)
        return 1

    if args.mode == "incremental":
        with open(args.existing, "r", encoding="utf-8") as fh:
            mindmap = json.load(fh)
        tracked = json.load(open(args.tracked, "r", encoding="utf-8")) if args.tracked and Path(args.tracked).exists() else None
        changes = detect_changes(mindmap, tracked, current_files)
        print(f"[plan] kb={args.root_name} mode=incremental 当前文件数={len(current_files)}", file=sys.stderr)
        print(f"[plan] 新增 {len(changes['added_files'])} 个文件: "
              f"{', '.join(f['filename'] for f in changes['added_files'][:20]) or '无'}"
              f"{' …' if len(changes['added_files']) > 20 else ''}", file=sys.stderr)
        print(f"[plan] 删除 {len(changes['removed_file_ids'])} 个文件（纯树手术，无 AI）", file=sys.stderr)
        if not changes["needs_update"]:
            print("[plan] 无变更，导图已是最新，无需执行。")
            return 0
        if not args.apply:
            print("[plan] 未加 --apply，仅打印计划，不调 LLM、不写文件。确认后加 --apply 执行。", file=sys.stderr)
            return 0

        if args.tree_json:
            # Agent 直抽：Agent 已基于变更整合好完整树（含新增、删除）
            with open(args.tree_json, "r", encoding="utf-8") as fh:
                mindmap = json.load(fh)
            tracked = {fid: info.get("filename", "") for fid, info in current_files.items()}
            print(f"[plan] 使用 Agent 提供的树 {args.tree_json}（跳过 LLM）", file=sys.stderr)
        else:
            # 批处理：删除纯树手术（无 LLM）+ 新增 LLM 整合
            if changes["removed_file_ids"]:
                removed_filenames = {tracked[fid] for fid in changes["removed_file_ids"] if tracked and fid in tracked}
                mindmap = remove_files(mindmap, removed_filenames) or {"content": args.root_name, "children": []}
                if tracked:
                    for fid in changes["removed_file_ids"]:
                        tracked.pop(fid, None)
            if changes["added_files"]:
                if not args.api_key:
                    print("error: 有新增文件但缺少 --api-key / OPENAI_API_KEY（或使用 --tree-json 由 Agent 整合）",
                          file=sys.stderr)
                    return 1
                mindmap = update_incremental(mindmap, changes["added_files"], args.root_name,
                                             args.user_prompt, args.api_base, args.api_key, args.model)
                if tracked is None:
                    tracked = {}
                for f in changes["added_files"]:
                    tracked[f["file_id"] or f["filename"]] = f["filename"]
        metadata = {
            "generated_at": datetime.now(UTC).isoformat(),
            "file_count": len(tracked or {}),
            "incremental": True,
        }
    else:
        # 全量模式
        overwrite_note = "（将覆盖现有 mindmap.json / tracked.json）" if args.existing and Path(args.existing).exists() else ""
        print(f"[plan] kb={args.root_name} mode=full 将全量重建导图：{len(files_info)} 个文件 {overwrite_note}",
              file=sys.stderr)
        if not args.apply:
            print("[plan] 未加 --apply，仅打印计划，不调 LLM、不写文件。确认后加 --apply 执行。", file=sys.stderr)
            return 0
        if args.tree_json:
            with open(args.tree_json, "r", encoding="utf-8") as fh:
                mindmap = json.load(fh)
            print(f"[plan] 使用 Agent 提供的树 {args.tree_json}（跳过 LLM）", file=sys.stderr)
        else:
            if not args.api_key:
                print("error: 需要 --api-key 或环境变量 OPENAI_API_KEY（或使用 --tree-json 由 Agent 生成树）",
                      file=sys.stderr)
                return 1
            mindmap = generate_full(files_info, args.root_name, args.user_prompt,
                                    args.api_base, args.api_key, args.model)
        tracked = {f["file_id"] or f["filename"]: f["filename"] for f in files_info}
        metadata = {
            "generated_at": datetime.now(UTC).isoformat(),
            "file_count": len(tracked),
            "incremental": False,
        }

    errors = validate(mindmap)
    if errors:
        print("warn: 生成树未通过校验：", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)

    Path(args.output).write_text(json.dumps(mindmap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.tracked_output).write_text(json.dumps(tracked or {}, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    leaf_count = len(collect_leaf_filenames(mindmap))
    print(f"ok: {len(files_info)} 个文件 -> 导图 {leaf_count} 个叶子节点 -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
