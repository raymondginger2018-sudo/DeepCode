---
name: hacker101-ctf-farming
description: >
  Hacker101 CTF 刷题/清场方法论（2026-08 实战沉淀，账户 276 分、26 挑战清 22、约 40+ flag）。
  覆盖：平台机制与坑（实例分配错乱/会话过期/提交可靠性）、派发子代理的节流守则、
  各挑战类型（Web/API/GraphQL/Android APK/Crypto/Recon）的高效打法、以及全程教训。
  适用于：在 ctf.hacker101.com 刷分、给客户/自己刷 Hacker101、或任何 CTF 靶场批量清场。
whenToUse: >
  用户要求"刷 Hacker101/CTF"、"打某挑战"、"继续刷题"、"凑分解锁私密项目"、
  "总结 CTF 经验"时使用。也可作为任何 CTF/靶场批量刷题任务的通用打法参考。
---

# Hacker101 CTF 刷题方法论

> 实战来源：2026-08 三场战斗（84→276 分，22 挑战全清）。本 Skill 固化平台机制、
> 子代理协作模式和各类挑战打法。**核心信条：以分数为准，不要信显示；以内容为准，不要信 id。**

## 0. 角色分工（铁律）

- **子代理**：只负责找 flag + 写工作记录，**绝不登录 HackerOne、绝不提交 flag**。
- **父代理（本 Agent）**：负责启动/终止实例、下载 APK、**提交 flag**、存档小脑。
- 子代理用后台通道（`subagent` run_in_background）最稳；`subagent_deepcode` 偶发取消、SDK 通道会超时。

## 1. 平台机制（踩过的坑，全部实测）

### 实例分配
- **start/<id> 分配会错乱**：同一个 id 可能给到别的挑战内容（如 21→oauth.apk、22→webdev.apk、44→Grinch 系）。
  判据：**以实例首页内容 / APK 文件名为准，不要信列表行的名字**。
- **同时只允许 1 个活动实例**：启动新挑战前必须先 terminate 旧实例（`/ctf/stop/<id>`）。
- **可靠启动流程**：`POST /ctf/start/<id>` → 轮询 `GET /ctf/activate_level/<id>` 拿真实实例 URL。
- **实例有时效**：返回 "404: Level not found" = 实例过期/下线 → terminate + 重新 start（新 URL，flag 值不变）。
- **APK 类挑战**：实例页先显示 "Your Android APK is building"（约 1-2 分钟），构建完出现下载链接 `<name>.apk`。
- **Restart 不丢 flag（官方确认）**：实例卡住/变慢时点 Restart 安全，已捕获 flag 全保留。
- **Hints 机制（官方）**：每关 3-5 个 hint，后续 hint 的解锁计时器递增（卡关可用，不必硬钻）。
- **Groups（官方）**：可建组邀请成员，管理进度（多人协作/教学场景）。

### 会话与网络
- **ERR_TOO_MANY_REDIRECTS on /ctf = 会话过期** → `GET /auth/login` → 点击 `form[action="/auth/authenticate"]` 的 submit（自动 OAuth 回跳）。
- 浏览器偶发 `ERR_PROXY_CONNECTION_FAILED`/`chrome-error` = 瞬时网络波动 → 直接重试即可。
- 会话重置后 playwright 是新上下文，需重登。

### flag 提交（最关键！）
- **不要用表单点击（page.click submit）提交 flag —— 会偶发失灵导致 flag 根本没提交**（曾因此差点漏 2 分）。
- **可靠方式**：页面取 CSRF token 后 `fetch POST https://ctf.hacker101.com/ctf/check_flag`
  （FormData: `authenticity_token` + `flag`，credentials include）。
- 响应判断：`Congratulations, you found a flag!` = 新接受；`claimed before` = 已入账；
  `not a valid flag` = 无效。无反馈 = 没提交成功。
- **以分数和挑战行 completion 为准**（行可能滞后数小时，最终同步；Hackyholidays 从 0/12 滞后到 12/12）。
- flag 可只提交 hex 部分（`^FLAG^...$FLAG$` 的中间 64hex）。

## 2. 子代理协作与节流守则（强制）

派发任何刷题子代理时，prompt 必须包含：
1. **节流**：请求间隔 ≥1-2 秒、串行、禁止并发洪泛；每阶段 ≤200 请求预算，超了停手换思路；随机抖动延迟。
   （原因：API 型实例有 IP 限速封禁，猛烈 fuzz 会触发 ~20min+ 封禁。）
2. **先文档后 fuzz**：优先 swagger/openapi.json、introspection、源码、JS bundle 拿端点清单，再逐个测。
3. **高效攻击优先**：UNION/报错注入 > 逐字符盲注；能读源码就不猜；能下载 APK 静态分析就不动态。
4. **避免破坏性操作**：上传 502 的畸形内容会让实例下线（Photo Gallery 教训）——测试上传先小后大。
5. **工作记录**：要求写本地 markdown（`worklogs/hacker101-<challenge>-<date>.md`），含请求/响应证据。
6. **交付格式**：flag 值 + 漏洞类型 + 可复现步骤 + 已排查面 + 卡点 + 记录路径。

子代理排队/滞后汇报处理：以 `check_flag` 响应和分数为准回复它，避免无限循环（可明确"任务结束，勿再汇报"）。

## 3. 各挑战类型打法（实战验证）

### Web 通用（博客/CMS/商城/工单）
- 枚举：robots.txt、注释、JS、隐藏路径；**先读源码注释/JS bundle**（常直接泄端点/密钥/flag）。
- 经典 flag 分布：IDOR（ID 枚举/编辑删除无鉴权）、SQLi（登录/参数，优先 UNION/报错）、存储/反射 XSS、
  逻辑缺陷（价格篡改/科学计数法/状态机）、无认证管理页、LFI（include 拼接 .php）。
- **SSTI 模板注入**：找 {{...}} 渲染点（预览/邮件模板功能），`{{template:文件名.html}}` 可读任意模板/文件。
- **XXE 任意文件读**：XML 解析端点（配置/导入），`<!ENTITY xxe SYSTEM "/path">` + 回显字段读源码。
- **自定义请求头鉴权**：403 页面/API 靠自定义头保护，头值常泄露在公开 JS 里（如 `X-SAFEPROTECTION: <base64>`）→ 直接带头上。
- **LFI → RCE 链**：include 白名单可被 `http:` 前缀绕过 + 存储内容（评论/上传）二次解析执行 → 读任意源码。
- 案例：Postbook（7 flags：IDOR 读/发/改/删 + md5 会话伪造 + 弱口令 + 隐藏帖 945）；
  Petshop（价格篡改 cart JSON）；TempImage（路径穿越检测泄露 + 任意上传 RCE）；
  Cody's First Blog（LFI+二次解析 RCE）；Model E1337（XXE 读源码）；Hackyholidays（Hate Mail SSTI）。

### 特殊执行模型（少见但会出现）
- **无头浏览器渲染型**（BugDB v1）：应用渲染用户脚本并返回截图 —— 提交 `fetch(location.href).then(r=>r.text()).then(t=>document.body.innerText=t)` 让渲染页显示源码；截图 OCR 易误读特殊字符，改为让脚本逐字符输出 **charCode 数字序列**再解码，无歧义。
- **攻击盒类**（Attack Box）：提交 payload 带 hash（`md5(salt+target)`，salt 从已知 payload 反推）；目标校验用黑名单（拦 127.x、"Invalid IP"）→ 用 **0.0.0.0**（Linux 上=本机）绕过 localhost 检测。

### API / GraphQL
- GraphQL：introspection 全量 → node()/find* 全局 ID 越权（base64 类型:ID）、对象类型差异（Bugs vs Bugs_）、
  mutation 匿名可写、字段级授权缺失。
- REST API：/api/v1/v2 枚举、swagger.json、X-Token/X-Session 跨版本复用、隐藏参数（?verbose）、
  目录遍历（../..%5c 混用反斜杠）、SSRF（avatar 等可 URL 字段 → 127.0.0.1 内网端点）。
- 案例：BugDB v2（node() IDOR）、Hackyholidays API（12 flags）、Y2Fu/247 File Dropper（3 flags）。

### Android APK（WebView 包装）
- 实例构建 APK → 下载 → **本地静态逆向**（无需模拟器）：
  `pip install androguard`；解包 zip；读 Manifest（exported/深链/cleartext/JS enable）；
  dex 反汇编找：硬编码密钥/flag、HMAC 签名逻辑、WebView JS 桥（addJavascriptInterface）、深链处理。
- 后端配合：APK 里的密钥/路径用于访问实例后端（如 HMAC 上传、SHA256 签名路径）。
- 案例：Thermostat（X-MAC 密钥 + X-Flag 头）、Intentional Exercise（硬编码密钥签 /appRoot/flagBearer）、
  Oauthbreaker（/oauth?redirect_url= 开放重定向 + JS 桥 getFlagPath() BF 解码隐藏文件）、
  Mobile Webdev（HMAC 上传 flag1 + Zip Slip flag2）。

### Crypto / Math
- CBC padding oracle：错误页区分 padding 无效 vs 其他 → 逐块解密/伪造；自定义 base64 字母表。
- 滚动码：错误响应泄露期望码 + 每次推进状态 → 收集样本做**线性复杂度分析**：
  试 LCG/xorshift/LFSR；GF(2) 线性用 BM 算法/高斯消元恢复状态预测下一码（v1 64位 2样本，v2 48位 BM 96+样本）。
- 案例：Encrypted Pastebin（4 flags：traceback + padding oracle + IV 翻转 + SQLi 取 key）、
  Model E1337 v1/v2。

### Recon
- 挑战名常是提示（base64 解码 "Can you recon?"）；flag 藏在：robots.txt、HTML 注释、
  混淆 JS（拼 data-info 属性）、隐藏 App 的 IDOR（base64 JSON id）、图片 EXIF、.htaccess/备份。
- 案例：Grinch Networks（robots→s3cr3t-ar3a→jquery 注入 data-info→People Rater）。

## 4. 全局流程（清场流水线）

1. ctf 页确认登录态（重定向循环则重登）+ 当前活动实例（有 Terminate 的挑战）。
2. terminate 旧实例 → `start/<id>`（或 activate_level 流程）→ 轮询拿实例 URL。
3. 若 APK 挑战：等构建 → 下载 APK。
4. 派后台子代理（完整 prompt：背景/实例 URL/已入账 flag 清单避免重复/节流守则/工作记录要求）。
5. 子代理返回 flag → **fetch check_flag 提交** → 确认分数与行 completion。
6. 存档小脑（`hacker101-ctf-progress-<date>`：分数、完成清单、缺口、机制笔记）。
7. 重复 2-6 直到队列清空。

## 5. 教训速查（防再犯）

- 破坏性测试会杀实例 → 先提交已得 flag 再继续。
- IP 限速封禁 → terminate 重启换新实例（flag 是挑战常量，不变）。
- 表单提交会失灵 → 永远用 fetch check_flag。
- 挑战 id 与内容不符 → 以实例内容判断。
- completion 滞后 → 以分数为准。
- 子代理循环汇报滞后信息 → 给最终确认 + 分数证据，明确收工。
- 同时只 1 个实例 → 换挑战先 terminate；H1 Thermostat 逆向时实例被终止无影响（APK 在本地）。
- 网络/浏览器波动 → 重试；会话掉 → 重登。

## 6. 覆盖核对表（276 分全场景自查）

> 用本 SKILL 刷题前先过一遍：遇到清单里的模式直接对号入座，未知模式打完后**补进本表**。

| # | 模式 | 覆盖位置 | 代表实战 |
|---|---|---|---|
| 1 | IDOR / SQLi / XSS / 逻辑缺陷 / 上传 | §3 Web 通用 | Postbook、Petshop、TempImage |
| 2 | SSTI 模板注入（{{template:...}}） | §3 Web 通用 | Hackyholidays Hate Mail |
| 3 | XXE 任意文件读 | §3 Web 通用 | Model E1337（/set-config） |
| 4 | 自定义请求头鉴权（密钥头泄露） | §3 Web 通用 | XSS Playground、OSU |
| 5 | LFI → RCE 链（http: 绕过 + 二次解析） | §3 Web 通用 | Cody's First Blog |
| 6 | 无头浏览器渲染 + OCR charCode 解码 | §3 特殊执行模型 | BugDB v1 |
| 7 | 攻击盒类（salt+md5 预测、0.0.0.0 绕过） | §3 特殊执行模型 | Hackyholidays Attack Box |
| 8 | GraphQL（introspection / node() / 类型差异 / mutation） | §3 API/GraphQL | BugDB v2、Hackyholidays API |
| 9 | REST API（swagger / 跨版本 token / SSRF / 目录遍历） | §3 API/GraphQL | Y2Fu(247)、Hackyholidays |
| 10 | Android APK 静态逆向（密钥 / HMAC / JS 桥 / 深链） | §3 Android APK | Thermostat、Intentional、Oauthbreaker、Webdev |
| 11 | Crypto（padding oracle / IV 翻转 / 自定义 base64） | §3 Crypto/Math | Encrypted Pastebin |
| 12 | Math（滚动码 LFSR / BM 线性分析 / 高斯消元） | §3 Crypto/Math | Model E1337 v1/v2 |
| 13 | Recon（robots / 注释 / 混淆 JS / 隐藏 App） | §3 Recon | Grinch 系 |
| 14 | 平台机制（分配错乱 / 会话 / 实例时效 / APK 构建） | §1 | 全程 |
| 15 | 提交可靠性（fetch check_flag / 响应语义） | §1 | 全程 |
| 16 | 节流守则 + 子代理协作 + 破坏性测试规避 | §2 | 全程 |

**经验法则**：新增实战出现的新模式 → 更新 §3 对应类型 + 本表加一行，保持 SKILL 自我进化。

