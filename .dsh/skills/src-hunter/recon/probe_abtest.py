#!/usr/bin/env python3
"""深挖 faas-abtest-server 公开实验配置端点"""
import urllib.request
import urllib.error

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
            return resp.status, b[:500].decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        b = e.read()
        return e.code, b[:500].decode("utf-8", errors="replace")
    except Exception as e:
        return "ERR", str(e)

tests = [
    ("POST", "https://www.temu.com/api/faas-abtest-server/firefly-api/exp-config", "{}"),
    ("POST", "https://www.temu.com/api/faas-abtest-server/firefly-api/exp-config", '{"project":"pc"}'),
    ("POST", "https://www.temu.com/api/faas-abtest-server/firefly-api/exp-config", '{"scene":"homepage"}'),
    ("GET",  "https://www.temu.com/api/faas-abtest-server/firefly-api/exp-config?scene=homepage", None),
    ("GET",  "https://www.temu.com/api/faas-abtest-server/firefly-api/exp-list", None),
    ("POST", "https://www.temu.com/api/faas-abtest-server/firefly-api/exp-config", '{"scene":"homepage","project":"pc","app_version":"1.0"}'),
]
for m, u, d in tests:
    st, body = fetch(u, m, d)
    print("[%s] %s %s" % (st, m, u.split("temu.com")[1]))
    print("    " + body)
