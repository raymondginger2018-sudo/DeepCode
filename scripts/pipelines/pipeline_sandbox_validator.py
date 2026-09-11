#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
管道 4: 编辑 → 沙箱验证 → 安全写入
====================================
在 dryRun 预览后，对修改内容运行沙箱验证，通过后才正式写入。

工作流:
  1. AI: filesystem.read_file("config.json")
  2. AI: filesystem.edit_file("config.json", edits=[...], dryRun=True)
  3. AI: sandbox.sandbox_run_python("验证 diff 是否合法")
  4. AI: filesystem.edit_file("config.json", edits=[...])  ← 正式写入

本脚本作为 hooks 的辅助: beforeWrite 拦截，验证通过才放行。

Hook 配置:
  "beforeWrite": [{
    "matcher": "*.json",
    "type": "command",
    "command": "python3 F:/DEEPCODE/scripts/pipelines/pipeline_sandbox_validator.py {file_path}",
    "timeout": 15
  }]
"""
import sys, json
from pathlib import Path

VALIDATORS = {
    ".json": """
import json
with open("{path}", "r", encoding="utf-8") as f:
    data = json.load(f)
assert isinstance(data, (dict, list)), "JSON root must be dict or list"
print("JSON valid")
""",
    ".py": """
import py_compile
py_compile.compile("{path}", doraise=True)
print("Python syntax OK ")
""",
    ".yaml": """
import yaml
with open("{path}", "r", encoding="utf-8") as f:
    data = yaml.safe_load(f)
assert data is not None, "YAML is empty or invalid"
print("YAML valid ")
""",
    ".yml": """
import yaml
with open("{path}", "r", encoding="utf-8") as f:
    data = yaml.safe_load(f)
assert data is not None, "YAML is empty or invalid"
print("YAML valid ")
""",
}


def validate_file(filepath: str) -> dict:
    p = Path(filepath)
    suffix = p.suffix.lower()

    if suffix not in VALIDATORS:
        return {"ok": True, "skipped": True, "reason": f"No validator for {suffix}"}

    code = VALIDATORS[suffix].replace("{path}", str(p.resolve()).replace("\\", "\\\\"))

    try:
        # 直接本地执行验证，不依赖 MCP 沙箱
        ns = {}
        exec(code, ns)
        return {"ok": True, "file": filepath, "validator": suffix}
    except Exception as e:
        return {"ok": False, "file": filepath, "error": str(e), "validator": suffix}


def main():
    if len(sys.argv) < 2:
        print("Usage: pipeline_sandbox_validator.py <file_path>")
        sys.exit(1)

    filepath = sys.argv[1]
    result = validate_file(filepath)

    if result["ok"]:
        sys.stderr.write(f"[validator] PASS: {Path(filepath).name}\n")
    else:
        sys.stderr.write(f"[validator] FAIL: {Path(filepath).name} -- {result.get('error', 'unknown')}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
