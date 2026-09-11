# -*- coding: utf-8 -*-
"""临时对比: qwen2.5:3b vs phi4-mini 英文攻击面摘要质量 (海外 H1 场景)"""
import sys

sys.path.insert(0, r"F:\DEEPCODE\.deepcode\skills\src-hunter\tools")
import bbx_compress as b

# 典型海外 H1 场景: OAuth token 端点 + IDOR 风格的 API (纯英文)
raw = """POST /oauth/token HTTP/1.1
Host: api.example.com
Authorization: Basic Y2xpZW50X2lkOnNlY3JldA==
Content-Type: application/x-www-form-urlencoded
X-Requested-With: XMLHttpRequest

grant_type=refresh_token&refresh_token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMDAxIiwicm9sZSI6InVzZXIifQ.sig

HTTP/1.1 200 OK
Content-Type: application/json
Server: nginx/1.24.0
Set-Cookie: session=abc123; Path=/; HttpOnly

{"access_token":"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMDAxIiwicm9sZSI6InVzZXIifQ.sig","token_type":"Bearer","expires_in":3600}

GET /api/v1/users/1002/profile HTTP/1.1
Host: api.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMDAxIiwicm9sZSI6InVzZXIifQ.sig

HTTP/1.1 200 OK
Content-Type: application/json

{"userId":1002,"email":"victim@example.com","role":"admin","twoFAEnabled":true}"""

compressed = b.compress(raw)

PROMPT = (
    "You are a bug bounty assistant. From the compressed HTTP capture below, "
    "list up to 5 attack surface findings in English: endpoints, parameters, "
    "auth mechanisms, suspicious fields, injection points. Be concise, one "
    "finding per line, no preamble.\n\n" + compressed[:4000]
)

print("=" * 60)
print("压缩后文本 (规则压缩):")
print(compressed)
print("=" * 60)

for model in ("qwen2.5:3b", "phi4-mini"):
    print(f"\n--- 模型: {model} ---")
    out = b._ollama_generate(PROMPT, model=model, max_tokens=400, timeout=120)
    print(out if out else "(空响应)")
    print("---")
