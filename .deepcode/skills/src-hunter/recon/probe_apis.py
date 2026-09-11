#!/usr/bin/env python3
"""对 www.temu.com 提取的 API 做低强度未授权访问探测（每次 1 个请求，带测试头）"""
import json
import urllib.request
import urllib.error

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "X-HackerOne-Research": "raymondginger",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.temu.com/",
}

TARGETS = [
    # (方法, 路径, 说明)
    ("GET",  "/api/bg-barbera-api/user/short/profile", "用户简档(未授权?)"),
    ("GET",  "/api/bg/sigerus/auth/login_name/is_registered?login_name=test@test.com", "用户枚举"),
    ("GET",  "/api/bg/sigerus/auth/history_login_info/query", "历史登录信息"),
    ("GET",  "/api/bg/elmar/local/query/mall_mail_mobile", "邮箱/手机查询"),
    ("GET",  "/api/bg/buffon/asset/centre/balance", "资产余额"),
    ("GET",  "/api/bg/freud/user/crypt/query", "KYC加密数据"),
    ("GET",  "/api/galerie/file/signature", "文件上传签名"),
    ("GET",  "/api/galerie/image/signature", "图片上传签名"),
    ("GET",  "/api/v1/cdn/get-address", "CDN地址"),
    ("GET",  "/api/poppy/v1/goods_detail?goods_id=601099999999", "商品详情"),
    ("GET",  "/api/poppy/v1/search?keyword=phone", "搜索接口"),
    ("GET",  "/api/bg/huygens/region/list", "区域列表"),
    ("GET",  "/api/bg/huygens/region/phoneCodes", "电话区号"),
    ("GET",  "/api/bg/elmar/channel/query/all", "渠道查询"),
    ("GET",  "/api/bg/sigerus/email_suffix_list/recommend", "邮箱后缀"),
    ("GET",  "/api/bg/sigerus/account/lifecycle/security_questions/jump", "安全问题"),
    ("GET",  "/api/bg-barbera-api/privacy/protocol/configure", "隐私协议"),
    ("GET",  "/api/v1/sumer-log/key-use-data", "日志数据"),
    ("GET",  "/api/v1/open-api/project/info", "项目信息"),
    ("GET",  "/api/poppy/v1/ad_floating_layer", "广告浮层"),
    ("GET",  "/api/cusco/platform/chat/showQuickEntry", "客服入口"),
    ("GET",  "/api/oak/size_guide/render", "尺码指南"),
    ("GET",  "/api/seo/get_page_seo_data", "SEO数据"),
    ("GET",  "/api/server/_stm", "stm接口"),
    ("GET",  "/api/risk/account/suspend/need_appeal", "账户申诉"),
]

BASE = "https://www.temu.com"

for method, path, desc in TARGETS:
    url = BASE + path
    req = urllib.request.Request(url, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            snippet = body[:300].decode("utf-8", errors="replace").replace("\n", " ")[:220]
            print(f"[{resp.status}] {desc:28s} | {len(body):7d}B | {ctype[:40]}")
            print(f"    {url}")
            if snippet.strip():
                print(f"    {snippet}")
    except urllib.error.HTTPError as e:
        body = e.read()
        snippet = body[:200].decode("utf-8", errors="replace").replace("\n", " ")[:180]
        print(f"[{e.code}] {desc:28s} | {len(body):7d}B | {snippet}")
        print(f"    {url}")
    except Exception as e:
        print(f"[ERR] {desc:28s} | {e}")
        print(f"    {url}")
