#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""srchunter — src-hunter 本地工具箱统一入口 (省 token 专用).

把 bbx_compress / bbx_payload / bbx_report 三个工具收编为子命令:

  python srchunter.py compress < file.txt               # 规则压缩抓包 (可选 --llm 本地摘要)
  python srchunter.py payload search sqli mysql         # payload 检索
  python srchunter.py payload match < compressed.txt    # 压缩文本 → 自动推荐攻击面
  python srchunter.py payload waf "SQL/NoSQL注入"       # WAF 绕过变体
  python srchunter.py payload tools nmap                # 工具命令
  python srchunter.py report new --type idor-auth --endpoint "..."  # H1 报告草稿
  python srchunter.py report cvss [type]                # CVSS 速查表
  python srchunter.py report checklist                  # 提交前自检

纯标准库, 通过 subprocess 转发到 tools/ 下对应脚本, 参数原样透传。
"""
from __future__ import annotations

import os
import subprocess
import sys

_TOOLS = os.path.dirname(os.path.abspath(__file__))

_MAP = {
    "compress": "bbx_compress.py",
    "payload": "bbx_payload.py",
    "report": "bbx_report.py",
}

_USAGE = "用法: python srchunter.py <compress|payload|report> [参数...]"


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    tool = sys.argv[1]
    script = _MAP.get(tool)
    if script is None:
        print(f"未知子命令: {tool}\n可用: compress / payload / report\n{_USAGE}")
        return 2
    cmd = [sys.executable, os.path.join(_TOOLS, script)] + sys.argv[2:]
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
