#!/usr/bin/env python3
"""从 Temu modernjs bundle 中提取 API 端点（被动静态分析）"""
import re
import glob
import collections

api_paths = collections.Counter()
api_urls = collections.Counter()
hosts = collections.Counter()

for f in glob.glob("bundles/*.js"):
    try:
        js = open(f, encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    # 1. 相对 API 路径: /api/... /bg/... /seller/... 等
    for m in re.finditer(r'["\x27`](/[a-zA-Z0-9_\-/]{3,60})["\x27`]', js):
        p = m.group(1)
        if re.search(r"(api|bg|gateway|seller|ads|login|auth|user|order|pay|goods|search|home|mkt|h5|biz)", p, re.I):
            api_paths[p] += 1
    # 2. 完整 URL (temu/kwcdn 域)
    for m in re.finditer(r"https?://[a-zA-Z0-9._\-]+\.(?:temu|kwcdn)\.com[^\"\x27\s]*", js):
        api_urls[m.group(0)] += 1
    # 3. 域
    for m in re.finditer(r"https?://([a-zA-Z0-9._\-]+\.(?:temu|kwcdn)\.com)", js):
        hosts[m.group(1)] += 1

print("=== API 相对路径 TOP 120 ===")
for p, c in api_paths.most_common(120):
    print(f"{c:4d} {p}")

print("\n=== 完整 URL TOP 60 ===")
for u, c in api_urls.most_common(60):
    print(f"{c:4d} {u}")

print("\n=== 域 TOP 40 ===")
for h, c in hosts.most_common(40):
    print(f"{c:4d} {h}")
