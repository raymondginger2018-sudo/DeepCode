# -*- coding: utf-8 -*-
"""临时脚本: 分析当前会话日志结构, 提取可压缩的冗余内容"""
import json
import os

path = os.path.expanduser("~/.deepcode/projects/F-DEEPCODE/6febfd60-0226-4b42-84ae-bd8aaed20dfd.jsonl")
msgs = []
with open(path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            msgs.append(json.loads(line))
        except Exception:
            pass

print("总消息数:", len(msgs))
from collections import Counter
roles = Counter(m.get("role", "?") for m in msgs)
print("角色分布:", dict(roles))

sizes = []
for m in msgs:
    s = len(json.dumps(m, ensure_ascii=False))
    c = str(m.get("content"))[:60].replace("\n", " ")
    sizes.append((s, m.get("role"), c))
sizes.sort(reverse=True)
print("--- 最大的 8 条消息 ---")
for s, r, c in sizes[:8]:
    print(f"{s:>8} chars  [{r}]  {c}")

# 提取 tool 类型的消息内容 (最冗余的部分), 汇总大小
tool_msgs = [m for m in msgs if m.get("role") == "tool" or "tool" in str(m.get("type", ""))]
tool_total = sum(len(json.dumps(m, ensure_ascii=False)) for m in tool_msgs)
print(f"\ntool 类消息数: {len(tool_msgs)}, 总大小: {tool_total} chars")
