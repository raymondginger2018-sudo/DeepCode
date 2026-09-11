# Temu HackerOne — Phase 2 被动侦察汇总 (2026-08-13)

> 全部数据来自公开被动源（CertSpotter CT 日志 / CommonCrawl 索引 / DNS），未向目标发送任何探测请求。

## 1. 子域清单（CertSpotter，46 个唯一名，18 个可解析）

### In-scope（4/5）
| 子域 | IP | 备注 |
|---|---|---|
| www.temu.com | 151.101.2.58 (Fastly) | 主站 |
| seller.temu.com | 20.47.113.46 (Azure) | 商家后台 — **高价值** |
| ads.temu.com | 130.33.176.113 (Azure) | 广告 |
| temu.com | 151.101.2.58 (Fastly) | 根域 |

### 可解析但 Out-of-scope（需 H1 申请扩展后才能测）
- seller-eu.temu.com → 20.166.177.88 (EU 商家后台)
- influencer-program.temu.com → 20.85.184.221
- research.temu.com → 172.205.19.78
- doh.temu.com → 20.15.0.18 (DNS over HTTPS)
- gtm.temu.com / gtm-us.temu.com (Google Tag Manager)
- mxmail-* 系列（9 个邮件服务器，纯邮件基础设施，低价值）

### 未解析（已下线/内部）
- payssl.temu.com 系列 9 个（apissl/devsign/devssl/finsign/finssl/prodsign/prodssl/product/productsign）— 支付签名服务，曾存在
- file.temu.com / matk.temu.com / pftk.temu.com / thtk.temu.com

## 2. seller.temu.com 历史端点（CommonCrawl CC-MAIN-2026-30）

- `/login.html?login_scene=200&shop_region=211&shop_site=100&...&_x_sessn_id=<id>` — 登录（_x_sessn_id 出现在 URL 中）
- `/seller/login` — 备用登录入口
- `/registration.html?merchant_exclusive_invitation_code=<code>` — **商家邀请码注册**（3 个不同邀请码出现在公开 URL 中）
- `/policy-page.html?type=5/6/7&version=US1.0/EEA1.4/CA1.0/MX1.0`
- `/privacy-choices.html` / `/robots.txt`

## 3. www.temu.com 历史端点（462 URL 中筛出的非商品端点）

### API / 广告
- `/api/adx/cm/pixel-criteo` / `pixel-gamoshi` / `pixel-index` / `pixel-insticator` / `pixel-mgid` / `pixel-outbrain` / `pixel-pubmatic` / `pixel-taboola` — 广告追踪（携带 adx_uid / cm_user_id / id 参数）
- `/api/x/m/ix?cm_user_id=` — 广告索引

### 页面
- `/about_temu_detail.html?content=CI-000693a9893c1d47f4ec&category=2` — **content 参数为编码 ID，IDOR 候选**
- `/affiliate_recruit.html` / `/affiliate_seeding_entry.html` / `/affiliate_question.html` — 联盟营销
- 多 locale 前缀（/ae /ae-ar /am-en /en-gb 等）+ sitemap.html + support-center.html

## 4. 侦察结论与攻击面方向

| 优先级 | 方向 | 依据 |
|---|---|---|
| P0 | seller.temu.com 商家后台鉴权/业务逻辑 | in-scope + 邀请码注册链路 + 登录端点多 |
| P1 | www.temu.com `/about_temu_detail.html?content=` IDOR | content 参数可枚举性 |
| P1 | www.temu.com 广告 API 参数注入 | adx_uid/cm_user_id 客户端可控 |
| P2 | 支付链路（payssl 系列） | 已下线，仅作情报留存 |
| P3 | 联盟营销 (affiliate_*) 佣金逻辑 | 需要账号 |

## 5. 下一步（Phase 3 主动探测前置条件）

- [ ] 获取用户 HackerOne 用户名（测试头 `X-HackerOne-Research: <handle>` 必需）
- [ ] 确认可对 in-scope 4 个域名发包
- [ ] 对 out-of-scope 子域测试需先在 H1 提交 scope 扩展请求
