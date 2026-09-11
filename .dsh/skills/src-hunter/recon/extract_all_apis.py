#!/usr/bin/env python3
"""全 bundle API 路径提取 — 落盘版（避免 bash 转义问题）"""
import re
import glob
import collections

paths = collections.Counter()
keywords = ('api|galerie|signature|upload|endpoint|auth|passport|bg/|yasuo|cdn|cos|general|sigerus|elmar|francis|huygens|poppy|uranus|freud|buffon|barbera|risk|oak|seo|server')

for f in glob.glob('bundles/*.js'):
    js = open(f, encoding='utf-8', errors='ignore').read()
    for m in re.finditer(r"""["'`](/[a-zA-Z0-9_\-/]{3,80})["'`]""", js):
        p = m.group(1)
        if re.search(keywords, p, re.I):
            paths[p] += 1

print('=== 所有 bundle 高价值 API 路径 TOP 120 ===')
for p, c in paths.most_common(120):
    print(f'{c:4d} {p}')
