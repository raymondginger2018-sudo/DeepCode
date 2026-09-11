#!/usr/bin/env python3
"""用 bundle 中发现的真实实验 ID 查询 faas-abtest 公开端点（低强度 3 请求）"""
import urllib.request
import urllib.error
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "X-HackerOne-Research": "raymondginger",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.temu.com/",
    "Content-Type": "application/json",
}

def fetch(url, method="GET", data=None):
    body_bytes = data.encode() if isinstance(data, str) else data
    req = urllib.request.Request(url, headers=HEADERS, method=method, data=body_bytes)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            b = resp.read()
            return resp.status, b.decode("utf-8", errors="replace")[:1200]
    except urllib.error.HTTPError as e:
        b = e.read()
        return e.code, b.decode("utf-8", errors="replace")[:1200]
    except Exception as e:
        return "ERR", str(e)

U = "https://www.temu.com/api/faas-abtest-server/firefly-api/exp-config"
tests = [
    '{"cpExpIdList":["265586"]}',
    '{"cpExpIdList":["265586","285649","272792"]}',
    '{"cpExpIdList":["265586","285649","272792"],"sceneKeyList":["homepage","goods_detail"]}',
]
for d in tests:
    st, body = fetch(U, "POST", d)
    print(f"[{st}] {d}")
    print("    " + body)
    print()
