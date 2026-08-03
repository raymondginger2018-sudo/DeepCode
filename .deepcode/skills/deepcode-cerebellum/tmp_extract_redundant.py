# -*- coding: utf-8 -*-
"""临时脚本: 提取会话中的冗余内容 (历史摘要链 + tool 消息), 供 compress_context 压缩"""
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

# 1) 提取所有历史摘要 (compacted summary)
summaries = []
for m in msgs:
    c = m.get("content", "")
    if isinstance(c, str) and "There are earlier parts of the conversation" in c:
        # 去掉开头的固定前缀, 只留摘要正文
        body = c.split("Here is a summary:", 1)[-1].strip()
        summaries.append(body)

print("历史摘要条数:", len(summaries))
print("每条摘要字符数:", [len(s) for s in summaries])
merged = "\n\n[--- 上一条摘要分割线 ---]\n\n".join(summaries)
print("合并后总字符数:", len(merged))

out = "F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/tmp_summaries.txt"
with open(out, "w", encoding="utf-8") as f:
    f.write(merged)
print("已保存到:", out)

# 2) 提取 tool 消息正文
tool_texts = []
for m in msgs:
    if m.get("role") == "tool":
        c = m.get("content")
        if isinstance(c, str):
            tool_texts.append(c)
        elif isinstance(c, dict):
            tool_texts.append(json.dumps(c, ensure_ascii=False))
tool_merged = "\n\n[--- tool 输出分割线 ---]\n\n".join(tool_texts)
out2 = "F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/tmp_tools.txt"
with open(out2, "w", encoding="utf-8") as f:
    f.write(tool_merged)
print("tool 消息条数:", len(tool_texts), "合并后字符数:", len(tool_merged))
print("已保存到:", out2)
