#!/usr/bin/env python3
"""带 cookie jar 重试 www.temu.com API + projectGroups 参数探测（低强度）"""
import http.cookiejar
import urllib.request
import urllib.error

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "X-HackerOne-Research": "raymondginger",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.temu.com/",
    "Accept-Language": "en-US,en;q=0.9",
}

# 先用 cookie jar 获取首页（模拟正常用户流程）
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
            body = resp.read()
            return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, body, dict(e.headers)
    except Exception as e:
        return None, str(e).encode(), {}

# 1. 先访问首页建立会话
st, body, hdrs = fetch("https://www.temu.com/")
print(f"[{st}] homepage | {len(body)}B | cookies: {[c.name for c in cj]}")
print("    Set-Cookie keys:", [k for k in hdrs if k.lower().startswith('set-cookie')])

# 2. 用建立的会话重试高价值 API
TARGETS = [
    ("GET", "/api/bg-barbera-api/user/short/profile", "用户简档"),
    ("GET", "/api/bg/sigerus/auth/login_name/is_registered?login_name=test@test.com", "用户枚举"),
    ("GET", "/api/bg/buffon/asset/centre/balance", "资产余额"),
    ("GET", "/api/galerie/file/signature", "文件上传签名"),
    ("GET", "/api/bg/elmar/local/query/mall_mail_mobile", "邮箱/手机查询"),
    ("GET", "/api/poppy/v1/ad_floating_layer", "广告浮层"),
    ("GET", "/api/v1/sumer-log/key-use-data", "日志数据"),
]

print("\n=== 会话重试 ===")
for method, path, desc in TARGETS:
    st, body, _ = fetch("https://www.temu.com" + path)
    snippet = body[:250].decode("utf-8", errors="replace").replace("\n", " ")[:200]
    print(f"[{st}] {desc:16s} | {len(body):6d}B | {snippet}")

# 3. projectGroups 参数探测
print("\n=== projectGroups 参数探测 ===")
for pg in ["adx", "homepage", "main", "web", "pc", "default"]:
    st, body, _ = fetch(f"https://www.temu.com/api/v1/cdn/get-address?projectGroups={pg}")
    snippet = body[:300].decode("utf-8", errors="replace").replace("\n", " ")[:260]
    print(f"[{st}] projectGroups={pg:10s} | {len(body):6d}B | {snippet}")

# 4. cdn/get 探测
st, body, _ = fetch("https://www.temu.com/api/v1/cdn/get")
snippet = body[:300].decode("utf-8", errors="replace").replace("\n", " ")[:260]
print(f"\n[{st}] /api/v1/cdn/get | {len(body):6d}B | {snippet}")
