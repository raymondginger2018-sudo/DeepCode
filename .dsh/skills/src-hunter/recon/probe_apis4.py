#!/usr/bin/env python3
"""Phase 3 新发现端点快速验证 — 低强度（每端点 1 请求）"""
import urllib.request
import urllib.error

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "X-HackerOne-Research": "raymondginger",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.temu.com/",
}

TARGETS = [
    ("GET",  "/api/phantom/obtain_captcha", "验证码服务"),
    ("GET",  "/api/faas-abtest-server/firefly-api/exp-config", "A/B实验配置"),
    ("GET",  "/api/bg/tampa/web_device/record", "设备指纹(GET)"),
    ("GET",  "/api/vela/energy_efficiency/tabs", "能效标签"),
    ("GET",  "/api/sakura_v2/floating/get", "sakura_v2"),
    ("GET",  "/api/poppy/v1/static/opt_list_all/en/100", "静态配置"),
    ("GET",  "/api/alexa/pc/homepage/activity", "首页活动"),
    ("GET",  "/api/v3/rubicon/benefit/query", "rubicon权益"),
    ("GET",  "/api/jade/hobbiton/conversion/user_privacy_setting", "隐私设置"),
    ("GET",  "/api/bg/sigerus/auth/pub_key/request", "登录公钥"),
    ("GET",  "/api/bg/sigerus/mobile_rule/get", "手机规则"),
    ("GET",  "/api/cusco/platform/chat/showQuickEntry", "客服入口"),
]

BASE = "https://www.temu.com"
for method, path, desc in TARGETS:
    url = BASE + path
    req = urllib.request.Request(url, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read()
            snippet = body[:180].decode("utf-8", errors="replace").replace("\n", " ")[:150]
            print(f"[{resp.status}] {desc:24s} | {len(body):7d}B | {snippet}")
    except urllib.error.HTTPError as e:
        body = e.read()
        snippet = body[:180].decode("utf-8", errors="replace").replace("\n", " ")[:150]
        print(f"[{e.code}] {desc:24s} | {len(body):7d}B | {snippet}")
    except Exception as e:
        print(f"[ERR] {desc:24s} | {e}")
