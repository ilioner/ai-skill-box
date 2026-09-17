# Examples

本目录只演示工作流和数据结构，不提供任何课程的固定能力目录。

## 为什么不复制“大学生心理健康教学资料”

该资料目录是 Skill 的实验输入，不是模板课程。把其能力节点放进 Skill 会诱导宿主模型把示例内容套用到其他课程，因此这里只保留中性最小样例。

## 最小端到端测试

```bash
cd capability-graph-extractor
cp examples/minimal-draft.json /tmp/capability-graph-test.json
python scripts/assign_ids.py /tmp/capability-graph-test.json --write
python scripts/validate_graph.py /tmp/capability-graph-test.json --schema templates/graph.schema.json
python scripts/export_graph.py /tmp/capability-graph-test.json --out-dir /tmp/capability-graph-export --formats csv,mermaid,html
```

`minimal-draft.json` 故意不给节点和关系填写 ID，用于验证稳定 ID 脚本。内容是虚构的中性教学任务，不代表课程标准。

## 使用真实课程资料时

1. 先运行材料清单脚本。
2. 由宿主模型读取并理解资料，保存来源摘录。
3. 依据 `SKILL.md` 和 references 生成图谱草稿。
4. 运行确定性脚本。
5. 输出冲突、缺口和待人工确认项，不把示例当答案。
