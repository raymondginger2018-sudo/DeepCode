#!/usr/bin/env python3
"""Xiaomi Phase 4·B 无账号未授权扫描 — 低强度（每端点 1 请求）"""
import urllib.request
import urllib.error
import ssl

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

# (目标, 说明, 路径列表)
TARGETS = [
    ("https://admin.jr.mi.com", "MonKing 管理后台 (IP限制403)", [
        "/", "/login", "/admin", "/admin/", "/api", "/api/", "/static/",
        "/actuator", "/actuator/health", "/swagger-ui.html", "/v2/api-docs",
        "/druid", "/nacos", "/robots.txt", "/favicon.ico", "/index.html",
    ]),
    ("https://coupon-center.pay.xiaomi.com", "优惠券中心 (Java mipaycommon)", [
        "/", "/actuator", "/actuator/health", "/actuator/env",
        "/swagger-ui.html", "/swagger-ui/index.html", "/v2/api-docs",
        "/v3/api-docs", "/coupon", "/coupon/list", "/api/coupon/list",
        "/api/", "/error", "/sts", "/monitor", "/health",
    ]),
    ("https://b.pay.xiaomi.com", "商户端 (Java cashpay-merchant)", [
        "/", "/actuator", "/actuator/health", "/actuator/env",
        "/swagger-ui.html", "/v2/api-docs", "/api/", "/merchant",
        "/merchant/", "/error", "/sts", "/health", "/login",
    ]),
    ("https://sentry.pay.xiaomi.com", "Sentry 服务 (403)", [
        "/", "/api/0/", "/api/0/internal/health/", "/_health/",
        "/auth/login/", "/manage/", "/robots.txt", "/api/",
    ]),
    ("https://cc.jr.mi.com", "CAS 客服 (MonKing)", [
        "/", "/login", "/api", "/api/", "/health", "/actuator",
        "/swagger-ui.html", "/error", "/static/", "/index.html",
    ]),
]

ctx = ssl.create_default_context()

def fetch(base, path):
    url = base + path
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "")[:45]
            loc = resp.headers.get("Location", "")[:90]
            return resp.status, len(body), ctype, loc
    except urllib.error.HTTPError as e:
        body = e.read()
        ctype = e.headers.get("Content-Type", "")[:45]
        loc = e.headers.get("Location", "")[:90]
        return e.code, len(body), ctype, loc
    except Exception as e:
        return "ERR", 0, "", str(e)[:80]

for base, desc, paths in TARGETS:
    print(f"\n########## {base} — {desc} ##########")
    for p in paths:
        st, size, ctype, loc = fetch(base, p)
        flag = ""
        if st == 200 and size > 50:
            flag = "  <<< 200 有内容"
        elif st not in (302, 403, 404, 405, 400):
            flag = f"  <<< 异常状态 {st}"
        print(f"[{st:>5}] {size:>8}B | {ctype:45s} | {p}{flag}")
        if loc:
            print(f"        → {loc}")
