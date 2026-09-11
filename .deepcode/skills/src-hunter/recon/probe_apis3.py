#!/usr/bin/env python3
"""POST 探测 /api/v1/cdn/get + projectGroups 格式猜测（低强度）"""
import http.cookiejar
import urllib.request
import urllib.error
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "X-HackerOne-Research": "raymondginger",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.temu.com/",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
}

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def fetch(url, method="GET", data=None, extra_headers=None):
    h = dict(HEADERS)
    if extra_headers:
        h.update(extra_headers)
    body_bytes = data.encode() if isinstance(data, str) else data
    req = urllib.request.Request(url, headers=h, method=method, data=body_bytes)
    try:
        with opener.open(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e).encode()

# 建立会话
fetch("https://www.temu.com/")

print("=== POST /api/v1/cdn/get ===")
for payload in [
    "{}",
    '{"projectGroups":["web"]}',
    '{"projectGroups":"web"}',
    '{"projectGroups":1}',
    '{"project":"web"}',
    '{"group":"web"}',
]:
    st, body = fetch("https://www.temu.com/api/v1/cdn/get", method="POST", data=payload)
    snippet = body[:300].decode("utf-8", errors="replace").replace("\n", " ")[:260]
    print(f"[{st}] POST {payload:35s} | {len(body):5d}B | {snippet}")

print("\n=== GET /api/v1/cdn/get-address 格式猜测 ===")
for pg in ["1", "0", "all", "web,pc", "web,pc,h5", "biz", "mkt", "goods", "bg", "gateway"]:
    st, body = fetch(f"https://www.temu.com/api/v1/cdn/get-address?projectGroups={pg}")
    snippet = body[:300].decode("utf-8", errors="replace").replace("\n", " ")[:240]
    print(f"[{st}] pg={pg:12s} | {len(body):5d}B | {snippet}")
