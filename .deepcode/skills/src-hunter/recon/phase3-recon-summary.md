# Temu Phase 3 主动侦察汇总（2026-08-13）

> 研究者：raymondginger（`X-HackerOne-Research: raymondginger`）
> 阶段：Phase 3 · Enum（低强度主动探测，所有请求 ≤3 次/端点，1 次/实验）
> 状态：**未发现可提交漏洞**，未认证攻击面基本穷尽

## 1. 存活确认与技术指纹（httpx + 手动验证）

| 域名 | IP / 托管 | 特征 |
|---|---|---|
| www.temu.com | 151.101.2.58 (Fastly) | Cloudflare + Bot Management，HSTS，HTTP/3 |
| seller.temu.com | 20.47.113.46 (Azure) | HSTS，HTTP/3，登录页 → /no-auth.html |
| ads.temu.com | 130.33.176.113 (Azure) | HSTS，HTTP/3，独立 Login 页 |
| temu.com | 151.101.2.58 (Fastly) | 301 → www.temu.com |

## 2. 前端架构（静态分析）

- SSR + 全内联 JS（首页 13 个 script 均无 src），业务代码经 ModernJS 运行时动态加载
- 19 个 bundle 通过 `static.kwcdn.com/m-assets/assets/modernjs/` 下载（www.temu.com 直连被 Cloudflare 302 拦截，CDN 域放行）
- bundle 总大小 3.2MB，已保存至 `recon/bundles/`

## 3. API 端点资产（120+，extract_all_apis.py）

微服务地图：
- **sigerus** — 认证（登录/注册/2FA/密码找回/用户枚举 `login_name/is_registered`）
- **francis** — 验证码（`verification/code/send|verify/{mail,mobile}`）
- **elmar** — 账号管理（`local/query/mall_mail_mobile` 邮箱手机查询）
- **huygens** — 区域（region list/phoneCodes）
- **poppy** — 商品/搜索/购物车
- **galerie** — 文件/图片上传（签名 + COS 分片）
- **yasuo-gateway** — OAuth/SMS
- **tampa** — 设备指纹（`web_device/record`）
- **freud** — KYC（`user/crypt/query`）
- **buffon** — 资产（`asset/centre/balance`）
- **barbera** — 用户简档
- **faas-abtest-server** — A/B 实验（**唯一绕过 anti-content 的公开端点**）
- 其他：risk/phantom/cusco/rubicon/jade/hobbiton/alexa/vela/sakura_v2

## 4. anti-content 签名机制（核心防护）

- 所有 `/api/bg/*` 及多数业务端点要求 `anti-content` 签名头；缺失 → 403 `error_code:40003`，或 429 `406008`
- 机制：前端先带挑战值请求 `signatureUrl`（如 `/api/galerie/file/signature`，body `{bucket_tag}`）换取签名，再调用业务 API
- 挑战值由页面运行时外部注入（`setAntiContent(e)`，bundle 中无生成逻辑）→ **无法从静态分析复现**，需浏览器执行环境
- cookie jar / 会话重放不足以绕过

## 5. 探测结果（25 + 12 + 6 端点，低强度）

| 分类 | 结果 |
|---|---|
| 未授权访问 | 全部被 anti-content/登录态拦截（403/424/429）|
| 公开可达 | `/api/server/_stm`（服务器时间，低价值）；`faas-abtest-server/firefly-api/exp-config`（200 但返回空配置）|
| IDOR 候选 | `about_temu_detail.html?content=CI-...` — 差异仅 i18n 字典，`activityInfoData:null`，**已排除** |
| 上传家族 | `/api/galerie/*` 全部 429 保护 |
| 信息泄露 | Spring 异常类名（`HttpRequestMethodNotSupportedExceptionGET` 等）、`projectGroups` 参数名、错误码语义 —— 低价值 |

## 6. 情报资产（记录不测，多数 out-of-scope）

- **file.temu.com 上传服务族**：us/global/eu/uk/au/nz/ca → Azure 20.x（**out-of-scope**，需 scope 扩展才可测）
- **CSP 泄露第三方集成**：Stripe/PayPal/Braintree/Square/Forter/Paidy/Cardinal/SmartroPay/Mobilians/PagoEfectivo + Sentry/Amplitude/Facebook/Apple
- **内部测试域**：apistatic.temudemo.com
- **Cookie 约定**：`api_uid`、`region`、`language`、`currency`；网关头 `x-gateway-request-id`
- **真实实验 ID**（bundle 泄露）：265586 / 285649 / 272792

## 7. 结论与下一步

未认证 API 侦察已穷尽：WAF + anti-content + Cloudflare Bot Mgmt 三重防护下，未发现可复现、可提交的高价值漏洞。

**下一步选项（需用户决策）**：
1. **注册真实账号** → 登录态 + 有效签名后深挖业务逻辑（seller 后台 / 支付 / 订单 / IDOR 双账号越权）
2. **申请 HackerOne scope 扩展** → 覆盖 file.temu.com 上传基础设施（Azure 面）
3. **换目标** → 新 HackerOne 程序（如 Xiaomi，2026-08-09 有 intake）
4. **保持现状** → 将本阶段成果作为基础资产沉淀，择机复用

## 8. 产物清单（recon/）

- phase2-recon-summary.md / phase3 本文件
- bundles/（19 个 JS bundle）
- extract_apis.py / extract_all_apis.py / probe_apis*.py / probe_abtest*.py
- seller_noauth.html / ads_login.html / www_home.html / about_valid.html / about_invalid.html
- certspotter_temu.json / cc_seller.json / cc_www.json
