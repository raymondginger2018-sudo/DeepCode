---
name: bug-bounty-omnibus
description: >
  漏洞赏金全能总纲（2026-09 整合沉淀，含 src-hunter 合并）。融合：
  (1) src-hunter 19 个攻击 playbook + 305 payload + 2887 H1 案例 + 263 WAF 绕过
  (2) hacker101-ctf-farming CTF 实战 + zcode-re-analysis 逆向工具链
  (3) dsh-code-review 代码审计 + deepcode-cerebellum 记忆引擎
  (4) 实战经验：Bugcrowd/HackerOne 提交流程、Epic VDP、BitGo API、Gogo VDP
  覆盖全流程：选目标 → 研究 → 侦察 → 枚举 → 漏洞探测（19 playbook）→ 验证 → 报告 → 提交 → 跟进。
  适用于：HackerOne / Bugcrowd / SRC / 国产众测 全平台赏金任务。
whenToUse: >
  用户要求"做赏金/挖洞/审计某目标"、"选下一个赏金目标"、"提交报告"、"研究某程序"、
  "总结赏金经验"、"src 挖洞"、"怎么测某个 API"、"如何绕过 WAF"、"waf bypass"、
  "怎么挖某目标"、"WAF 绕过"、或任何涉及授权安全测试任务时使用。
  本 SKILL 是赏金任务的总纲，包含 src-hunter 的 19 个攻击 playbook。
  具体 CTF 打法可再引用 hacker101-ctf-farming 的 §3 漏洞模式库。
---

# 漏洞赏金全能总纲 (Bug Bounty Omnibus)

> 整合来源：hacker101-ctf-farming（CTF 实战 276 分）、zcode-re-analysis（ZCode 逆向）、
> dsh-code-review（harness 代码审查）、deepcode-cerebellum（记忆）、record-browser-gif（PoC 录制）、
> 以及 Node.js/Kubernetes 赏金实战（2026-08）沉淀的资格规则、报告模板与提交流程。

## 0. 合规红线（最高优先级，违反即出局）

1. **只测 in-scope 资产**：以程序 policy_scopes 为准，绝不碰 out-of-scope、第三方、未授权资产。
2. **不做破坏性操作**：无 DoS、无爆破、无大规模扫描；测试上传/注入先小后大；不删改他人数据。
3. **不公开披露**：一切走平台私密提交；禁止 GitHub issue/PR、社交媒体、邮件列表提前泄露。
4. **不社工、不碰凭据滥用、不测支付/隐私的越界面**（除非程序明确鼓励）。
5. **报告只含事实**：不写赏金预估、不含账户个人信息、不含对在线系统的探测记录。
6. 遵守程序额外规则（如"first to disclose"条款、修复前不公开、IBB 流程等）。

## 1. 目标选择与资格筛查（强制，先于一切）

**规则来源**：rule-bugbounty-eligibility-check-first（2026-08 教训：报告做完才发现账户提不了）。

选目标前 MUST 输出资格矩阵：
1. **程序公开性**：公开 vs 邀请制（Private 无邀请跳过）。
2. **程序类型**：Bounty（有赏金）vs Response Policy（仅致谢，非首选）。
3. **账户级资格**：trial 配额（H1 新账户 0/30 天滚动）、Signal、ID 验证状态。
4. **程序级限制**：居民/地域、年龄、账户要求、资产范围、特殊条款。
5. **其他**：最低赏金、竞争度、技能匹配（web/逆向/代码审计/APK）。

筛查工具：Playwright 抓程序页（Bounty/Response 标记、min bounty、policy_scopes 滚动加载资产）、
小脑记忆里的候选清单（hackerone_bug_bounty_programs_sorted 等）。

## 2. 目标研究（Phase 1 Intake）

抓取并解析程序政策页 + policy_scopes：
- **In scope**：仓库/域名/APK/API 清单（滚屏加载，注意"container 可写"等特殊条款）
- **Out of scope**：vendor 插件、第三方子域、排除资产
- **赏金表**：按严重级金额、修饰符（如"无补丁 -25%"→ 报告必带 Suggested fix）
- **响应预期**：平均响应时间、disclosure policy、是否需要 PoC
- 存档小脑（`hackerone-<program>-intake-<date>`）。

## 3. 侦察与信息收集

- **Web**：robots/注释/JS bundle（常泄端点/密钥）/隐藏路径/响应头/版本指纹。
- **API**：swagger.json、openapi、introspection、/api/v1/v2 枚举、跨版本 token 复用。
- **源码**：.git/.env/备份、sourcemap、调试端点、错误页 traceback。
- **资产关联**：子域名、CNAME、第三方集成（从 JS/证书/历史泄露找）。
- **APK/二进制**：下载 → 本地静态分析（见 §5 工具链）。
- **Recon 线索**：名字/邮箱/域名反查、图片 EXIF、历史版本对比。
- 全程节流：间隔 ≥1-2s、先文档后 fuzz、每阶段 ≤200 请求、随机抖动（防 IP 封禁）。

## 4. 漏洞模式库（src-hunter 19 playbook + hacker101 CTF 实战）

> **完整 playbook 目录**：`references/playbooks/`（19 个详细 playbook，每个含 H1 真实案例 + 结构化 payload）
> **305 条结构化 payload**：`references/payloader/`（按 web/内网分类，含 WAF 绕过变体）
> **263 步 WAF 绕过**：`references/payloader/waf-bypass.md`
> **国产指纹字典**：`references/dictionaries/chinese-srcfingerprints.md`

### 攻击类型 Playbook 索引（19 类）

| Playbook | 文件 | 入口提示 |
|----------|------|---------|
| 未授权访问 | `references/playbooks/unauth-access.md` | Actuator/Swagger/默认端口/弱密码 |
| 信息泄露 | `references/playbooks/info-disclosure.md` | .git/.env/heapdump/路径列举 |
| 任意 X 越权 | `references/playbooks/arbitrary-x-authz.md` | ID 可遍历/可修改 |
| 业务逻辑 | `references/playbooks/logic-flaws.md` | 密码重置/支付/订单/验证码 |
| OAuth/SAML/JWT | `references/playbooks/oauth-saml-jwt.md` | 认证流/redirect_uri/token |
| API REST | `references/playbooks/api-rest.md` | BOLA/Mass Assignment/速率 |
| SQLi | `references/playbooks/sqli.md` | 任何用户输入进 DB |
| RCE | `references/playbooks/rce.md` | 反序列化/SSTI/XXE/原型链 |
| SSRF | `references/playbooks/ssrf-cache-host.md` | URL 入参/缓存/Host 注入 |
| 路径遍历 | `references/playbooks/path-traversal.md` | 文件路径入参/LFI/RFI |
| 文件上传 | `references/playbooks/file-upload.md` | 上传点 + 解析漏洞 |
| XSS | `references/playbooks/xss.md` | 任何用户输入进 HTML/JS |
| HTTP 走私 | `references/playbooks/http-smuggling.md` | 反代 + Content-Length |
| GraphQL | `references/playbooks/graphql.md` | introspection/嵌套 |
| 竞态 | `references/playbooks/race-conditions.md` | 并发请求 / TOCTOU |
| DoS | `references/playbooks/dos.md` | ReDoS / 资源不限速 |
| 移动端 | `references/playbooks/mobile.md` | Android / iOS APK |
| LLM Agent | `references/playbooks/llm-prompt-injection.md` | Prompt 注入 / 工具调用 |
| 内网后渗透 | `references/playbooks/intranet-postexp.md` | 凭据 / 横向 / 域 |

### 行业垂直 Playbook

| 行业 | 文件 | 何时用 |
|------|------|--------|
| 银行/支付/金融 | `references/industry/banking-finance.md` | 目标含支付/网银/第三方支付 |
| 电信/ISP | `references/industry/telecom-isp.md` | 目标是运营商/BOSS/网管 |

### 工具命令速查
`references/payloader/tools/` 包含：web 渗透、内网渗透、信息收集、Windows 渗透、凭证窃取、域渗透、密码攻击、权限提升、漏洞利用、系统命令、红队工具、编码解码、隧道代理

### 高级漏洞类型（来自 gl0bal01/intel-codex）
- HTTP Smuggling、Cache Deception/Poisoning、Prototype Pollution
- JWT、SSTI、XS-Leaks、OAuth/SAML
- Race Condition（single-packet attack）、Price Manipulation、Mass Assignment、GraphQL

### hacker101 CTF 漏洞模式
- **Web**：IDOR / SQLi / XSS / SSTI / XXE / 逻辑缺陷 / 无认证管理页 / LFI→RCE
- **API/GraphQL**：introspection → node()/find* 全局 ID 越权 / mutation 匿名可写 / SSRF
- **Android APK**：硬编码密钥/flag、HMAC 签名、WebView JS 桥、深链
- **Crypto**：CBC padding oracle、IV 翻转、自定义 base64、滚动码
- **Native/SSRF**：HeadlessChrome 渲染 + fetch SSRF 到 localhost

## 5. 专用工具链

### 逆向（来自 zcode-re-analysis）
- 唤醒 MCP：`cerebellum_mcp_wake("ghidra-mcp")`（288 工具：PE 扫描/字符串/导入表/JS bundle/Ghidra 桥）、
  `cerebellum_mcp_wake("deepcode-decompiler")`（PE/字节码反编译/CFG）。
- Ghidra 桥：`ghidra-mcp connect_instance`（本机 127.0.0.1:8089）。
- Electron：`npx @electron/asar extract <app.asar> <outdir>`；单行 JS 先 prettier 再读。
- APK：`pip install androguard`；解包 zip；Manifest（exported/深链/JS enable）；dex 反汇编找密钥/flag/JS 桥。

### 代码审计（来自 dsh-code-review 思路）
- 优先正确性、生命周期、**安全**、破坏的必要行为，胜过风格。
- 关注 bug 类：子进程（shell 注入）、回调/异步状态、资源处置、输入校验边界、权限检查不对称
  （create vs update 分支）、通配符/路径语义（如 Node.js permission model 教训）。
- 引用具体文件+行；一个确凿的 blocker 胜过一堆 nit。

### 浏览器自动化（来自 record-browser-gif + playwright）
- 目标交互、会话管理（登录态）、PoC 复现；需要时录制 GIF 作为证据（state-based 帧捕获+确定性编码）。

## 6. 验证与 PoC

- **本地优先**：源码级静态验证 + 本地环境复现（go test/python 仿真）；绝不直接对生产打破坏性 payload。
- **最小触碰**：只做证明漏洞所需的请求；验证后清理测试数据/文件。
- **证据链**：记录请求、payload、响应原文、版本/commit、时间；双版本/对照组增强说服力。
- **节流与风控**：真实目标比靶场更严——低频率、串行、避免明显扫描特征。
- 沙箱：不可信代码/PoC 只在隔离环境执行。

## 7. 报告与提交（核心产出）

### 报告结构（评审友好，英文）
1. Title（≤150 字符，含 [Program] 前缀，点明越权后果）
2. Weakness（CWE，主选+备选）
3. Summary（3-5 句，30 秒看懂）
4. Vulnerability details（根因：代码位置+逻辑；触发条件）
5. Reproduction steps（精确可复制；命令+真实输出；附 PoC 附件/代码块）
6. Impact（读=泄露什么、写=能改什么、API 误导等；按 CWE/程序口径）
7. Suggested fix（**必带**——修饰符 -25% 教训）
8. Severity（保守下限+争取先例；CVSS 参考向量；引用同族 CVE/历史报告）
9. Attachments（PoC 文件、复现脚本；ASCII 文件名；附件受限则正文代码块兜底）

### 提交流程（2026-09-01 固化）

**提交前核对清单（MUST，全部通过才能提交）**：
1. ✅ **VRT Category 已选**（父级 > 子级，截图确认）
2. ✅ **Target 已选**（下拉框有值）
3. ✅ **Description 完整**（有 Summary + Repro + Impact + Fix）
4. ✅ **Terms 已勾选**
5. ✅ **附件已检查**（是否需要截图/PoC 脚本固化证据）
6. ✅ **报告内容无敏感信息**（不含赏金数字/个人账户密码）
7. ✅ **DEEPCODE 审查通过**（派 DEEPCODE 检查报告内容完整性、格式、遗漏）
8. ✅ **截图给你确认**（全页面截图，你确认后再提交）

**提交权在用户手中**：
- 我填写所有字段 → 通知你"表单已填好，你来提交"
- 你在 GUI 浏览器中检查 → 手动点击 Report vulnerability
- **不由我点击提交按钮**

**附件要求**：
- Bugcrowd 只支持 `.jpg/.gif/.png` 附件（不支持 .sh/.py）
- PoC 脚本内容放在 Description 正文中（代码块格式）
- 关键证据截图作为附件上传（.png 格式）
- 如果漏洞 URL 公开可复现，可以不加附件

**提交后**：
- 平台私密提交；正文不含赏金数字/个人信息
- 提交后**立即记录报告 ID + URL** 到 tracker（worklogs/h1-reports-tracker.md + 小脑）
- 检查页面是否跳转到程序主页（成功标志）

### triage 沟通
- 响应及时、专业；被要求补充时按需给证据；dup/关闭时复盘原因记入小脑经验。
- 引用同类先例（CVE 编号/历史报告）争取合理定级。

### 7a. Bugcrowd 提交实战经验（★ 2026-08-30 实战沉淀）

> 来源：BitGo Mobile Apps 报告提交全流程。**核心教训：Bugcrowd 表单是 React 组件，DOM 操作需要特殊技巧。**

**表单字段与填写方法**：

| 字段 | HTML ID/Name | 填写方法 | 注意事项 |
|------|-------------|---------|---------|
| Summary title | `submission[caption]` | `execCommand('insertText')` 或 Playwright `type` | React 组件，直接设 value 无效 |
| Target | `submission[target_id]` (select) | 设置 select value + dispatchEvent change | 自定义组件可能需要 listbox 交互 |
| VRT Category | `#vrt-form-input` (custom) | **点击 listbox → 选择 option** | 非标准 select，必须通过 role=listbox 操作 |
| Bug URL | `submission[bug_url]` | `execCommand('insertText')` | 同 caption |
| Description | 富文本编辑器 (contenteditable) | Playwright `fill` 或 `type` 到 textbox | 不是 textarea，是 ProseMirror |
| Terms | `submission[terms_and_conditions]` (checkbox) | `.click()` | 提交前必须勾选 |

**React 表单填写技巧**：
```javascript
// 方法1: execCommand（最可靠）
const nativeSet = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
nativeSet.call(input, newValue);
input.dispatchEvent(new Event('input', { bubbles: true }));

// 方法2: VRT 自定义下拉
const listbox = document.querySelector('[role="listbox"]');
const option = [...listbox.querySelectorAll('[role="option"]')].find(o => o.textContent.includes('目标值'));
option.click();

// 方法3: 富文本编辑器
const editor = document.querySelector('[contenteditable="true"]');
editor.innerHTML = '<p>Markdown content</p>';
editor.dispatchEvent(new Event('input', { bubbles: true }));
```

**提交流程**：
1. 填写所有必填字段（Caption, Target, VRT, Description, Terms）
2. VRT Category 必须通过 listbox 选择（点击按钮 → 选 option）
3. 点击 "Report vulnerability" 按钮
4. 成功标志：页面跳转到程序主页（不再停留在 /submissions/new）
5. 失败标志：仍在 edit 页面 + 无错误信息 = 缺少必填字段

**常见坑**：
- ❌ 直接设 React input 的 value = React 不会识别
- ❌ VRT 是自定义 listbox，不是 select
- ❌ Description 是富文本编辑器，不是 textarea
- ✅ 用 execCommand 或 Playwright fill/type 方法
- ✅ 提交后检查页面 URL 是否跳转

**HackerOne 提交技巧**（对比）：
- HackerOne 用 fetch POST + CSRF token（更可靠）
- Bugcrowd 用表单填写 + 按钮点击（更易失败）
- 两者都要求：提交后立即记录报告 ID

## 8. 工作流自动化

### 子代理协作（强制模式）
- 分工：子代理做侦察/审计/复现并写工作记录；父代理负责平台操作（提交/账户）与最终判断。
- prompt 必带：目标+范围红线、节流守则、已入账/已知信息清单（避免重复）、工作记录路径、结构化交付格式。
- 后台通道最稳；子代理滞后汇报 → 以第一手证据（check_flag 响应/分数/请求响应）为准回复。

### DEEPCODE 子代理使用规范（★ 2026-09-01 固化）

> **核心原则：DEEPCODE 只用于文件操作和代码任务，Playwright 操作由 DSH 直接执行。**

**允许的任务（✅）**：
- 文件读写（read/write/edit）
- 代码编写和修改
- 文件搜索（grep/glob）
- Shell 命令（简单任务）
- 文本分析和处理
- 网络请求（web_fetch）

**禁止的任务（❌）**：
- Playwright 浏览器操作（点击、填表、截图）
- 复杂多步任务（易超时）
- 需要登录态的网页操作
- 依赖 DSH 浏览器会话的任务

**原因**：
- DEEPCODE 的 Playwright 环境与 DSH 冲突
- 复杂任务易超时失败
- DEEPCODE 无头模式无法看到操作过程

**规范**：
1. **DSH 用户只调用 `subagent_deepcode`**，不直接使用 DEEPCODE CLI
2. **Playwright 操作由 DSH 直接执行**，不委派给 DEEPCODE
3. **复杂任务拆分成小步骤**，避免超时
4. **不要并发运行多个 DEEPCODE 子代理**

**历史教训**：
- 2026-08-29：权限问题（Ask 模式拒绝 bash）→ 已修复（--access full-access）
- 2026-08-30：会话文件过多导致启动卡死 → 已清理
- 2026-09-01：Playwright 操作失败 → 确认为环境冲突，非权限问题

### 小脑集成（deepcode-cerebellum）
- 强制规则已存：`rule-bugbounty-mandatory-work-log`（每次任务结束 MUST 存工作记录）、
  `rule-bugbounty-eligibility-check-first`（选目标先资格筛查）、`rule-ctf-rate-limit-avoidance`（节流）。
- 流程：Intake/工作记录/报告终稿/提交状态 → memory_save；踩坑 → experience_record；定期 Dreaming 巩固。

### DeepCode Agent SDK（deepcode-agent-sdk）
- 需要时以 HTTP API(8088)/MCP stdio/Python 嵌入模式运行 agent；工具注册/任务执行/文件/命令。

### 文件化规划（planning-with-files 模式，已内化为惯例）
> 借鉴 OthmanAdi/planning-with-files 的"三文件 + 每轮回注"思路；你的 DSH 用
> task_plan.md/progress.md（plan 后端）+ 小脑记忆等效实现，且更强（语义检索+跨会话）。

- **三文件惯例**（每目标一套，放 `worklogs/<program>/` 或 `bounty/<program>/`）：
  - `task_plan.md` — 目标、阶段（Intake/Recon/审计/验证/报告/提交）、下一步、完成门（确定性）。
  - `findings.md` — 发现/证据/排除项/负结果（假设→验证→防御层→结论）。
  - `progress.md` — 每轮结束追加一行：完成什么、卡在哪、下一轮第一步（崩溃/断会话可秒恢复）。
- **回注习惯**：每轮开始先读 task_plan/progress 定位"上一轮停在哪"，再继续；不重复已完成工作。
- 会话中断/上下文压缩 → 三文件 + 小脑 = 无损恢复（实战：多次会话中断后无缝续跑）。
- 与强制规则衔接：任务结束按 `rule-bugbounty-mandatory-work-log` 把三文件精华存档小脑。

## 9. 强制规则与教训速查

- 报告做好≠能提交：先查资格/trial 配额再投入（资格筛查规则）。
- 破坏性测试可能击穿靶场/触发风控：先拿已得成果再深入。
- 表单/按钮提交偶发失灵：平台操作用 API/可靠方式，以服务器响应+分数为准。
- 分配/映射错乱：以目标实际内容为准，不信名字/id。
- completion/状态滞后：以权威数据（分数、check_flag 响应、policy 页）为准。
- 子代理循环滞后汇报：给最终确认+证据，明确收工。
- 账户级配额（trial/Signal）：提升途径=CTF 得分（Hacker101 26 分解锁私密邀请）+ 有效报告积累。
- 长期记忆：所有产出（intake/记录/报告/经验）存小脑，跨会话可检索。

## 10. 覆盖自检（新实战后更新）

| 阶段 | 覆盖 | 来源 |
|---|---|---|
| 资格筛查/Intake | §1-2 | 原创 |
| 侦察/被动情报 | §3 + src-hunter Phase 2 | crt.sh/CT日志/Wayback/GitHub dorks |
| 主动枚举 | §3 + src-hunter Phase 3 | amass/subfinder/httpx/ffuf/wappalyzer |
| 漏洞探测（19 playbook） | §4 | src-hunter 19 playbook + 305 payload |
| WAF 绕过 | §4 | src-hunter 263 步骤 |
| 逆向/审计/APK | §5 | apk-reverse + zcode-re + ida-reverse |
| 验证/PoC | §6 | 原创 |
| 报告/提交/H1 案例 | §7 + §7a | 原创 + src-hunter 2887 H1 案例 |
| 国产 OA/中间件 | §4 字典 | src-hunter chinese-srcfingerprints |
| 行业垂直 | §4 行业 | src-hunter banking-finance/telecom |

**经验法则**：新漏洞类型/新平台机制/新教训 → 更新对应章节；保持 SKILL 自我进化。

## 11. 相关技能索引（DEEPCODE 主库 `.deepcode/skills/` + 本地 `references/`）

> 全能总纲管流程与合规。src-hunter 的 87 个参考文件已整合到 `references/` 目录。
> 下面这些是**按场景调用的专业 skill**（DEEPCODE 自带，直接按名加载）。

### references/ 目录结构（来自 src-hunter 合并）
```
references/
├── methodology/   6 docs   # 通用打法（攻击优先级/绕过/证据/控制缺口/时间盒）
├── playbooks/    19 docs   # 攻击类 playbook（每个含 H1 案例 + Payload 库）
├── industry/      3 docs   # 行业垂直（银行/电信）
├── dictionaries/  3 docs   # 字典/凭据（国产 OA 指纹 + 默认密码）
├── templates/     1 doc    # 报告模板
├── payloader/              # 305 条结构化 payload + 263 WAF 绕过
│   ├── by-category/        # web/内网分类 payload
│   ├── tools/              # 工具命令速查（13 类）
│   └── waf-bypass.md       # WAF 绕过集
└── tools/         1 doc    # MCP 工具集成（jshook）
```

### Web / API 应用安全
- `api-security` — REST/GraphQL/WebSocket/SOAP API 授权与认证评估（接口发现、限流、CI/CD 测试）
- `code-audit` — 源码安全审计 + SAST（Semgrep/CodeQL 规则、危险 API 挖掘、修复验证）
- `llm-security` — LLM/AI Agent 安全（提示注入、工具滥用、RAG 数据暴露、记忆投毒）
- `identity-federation` — SAML/OIDC/OAuth2/SSO 错误配置、token 混淆
- `email-security` — SPF/DKIM/DMARC、BEC、邮箱 token 滥用
- `database-security` — PostgreSQL/MySQL/MSSQL/Mongo/Redis 暴露面、UDF/命令路径
- `browser-automation` — Playwright 浏览器 + OpenReverse 桌面自动化（侦察/PoC 执行）
- `pentest-tools` — 主动渗透工具链（信息收集/扫描/注入/目录爆破）

### 逆向 / 二进制（二进制类赏金）
- `reverse-engineering` — 通用二进制逆向总纲（编译/混淆/加壳/虚拟化目标）
- `ghidra-reverse` / `ida-reverse` / `radare2` — 三大逆向工具（无 IDA 用 Ghidra headless）
- `go-rust-reverse` — 剥离符号的 Go/Rust 二进制（pclntab/module 恢复）
- `dotnet-reverse` — .NET/C# 程序集（含 NativeAOT、Sharp 系红队产物）
- `js-reverse` / `dsl-vm-reverse` — 前端 JS / 自定义 VM/DSL 引擎（风控字段、接口签名）
- `apk-reverse` / `mobile-reverse` — Android APK / iOS 逆向（smali、Frida、SSL pinning）
- `browser-extension-reverse` — Chrome/Firefox 扩展逆向（manifest、background worker、凭据逻辑）
- `firmware-pentest` — 固件/IoT（binwalk 提取→模拟→利用，OWASP FSTM）
- `binary-diff` / `patch-diff-exploit` — 跨版本符号迁移 / N-day 补丁差分到 PoC
- `pwn-chain` — 漏洞点到稳定 exploit 的工程化
- `protocol-reverse` — Protobuf/gRPC/WebSocket/PCAP 协议还原
- `macos-reverse` / `malware-analysis` — Mach-O 逆向 / 恶意样本 IOC 提取（防御侧视角）

### 基础设施 / 横向
- `cloud-k8s` — 云/容器/K8s（metadata SSRF、IAM、容器逃逸、RBAC）
- `windows-ad` — AD 与 Windows 身份攻击（Kerberos、AD CS、NTLM relay）
- `thick-client` — 桌面厚客户端（本地存储、更新通道、IPC、信任边界）
- `supply-chain-security` — SBOM/SCA/CI-CD/依赖可达性
- `hardware-security` / `ot-ics` / `radio-sdr` / `wifi-wireless` — 硬件/OT/无线（低频但存在）

### 流程 / 方法论
- `attack-chain` — 多阶段攻击路径规划（跨侦察→提权→横向的复杂任务）
- `sparc-methodology` — SPAR-C 渗透测试方法论
- `src-hunter` — 源码猎人打法（SRC 赏金专属）
- `triage` — 报告分类/优先级评估
- `docs-generator` — 任务结束时生成正式安全报告（逆向/渗透/CTF 通用）
- `digital-forensics` / `threat-hunting` — 取证/威胁狩猎（蓝队，验证性场景）

### 基础设施（agent 能力）
- `deepcode-cerebellum` — 记忆/经验/设置快照（本 SKILL §8 依赖它）
- `deepcode-sandbox` — 安全执行任意代码/命令（PoC 隔离运行）
- `deepcode-agent-sdk` / `deepcode-agent` — 以 Agent 模式被调用
- `deepcode-streaming` / `deepcode-telemetry` / `deepcode-knowledge` — 流式/遥测/知识库

### 场景 → skill 速查
| 遇到 | 调 |
|---|---|
| Web 功能测试 | api-security / browser-automation / hacker101-ctf-farming §4 |
| 源码审计 | code-audit |
| 二进制/固件/APK | reverse-engineering / ghidra-reverse / firmware-pentest / apk-reverse |
| N-day 补丁 | patch-diff-exploit |
| 云/容器 | cloud-k8s |
| 身份/AD | identity-federation / windows-ad |
| LLM 应用 | llm-security |
| 复杂多阶段 | attack-chain / sparc-methodology |
| 写报告 | docs-generator + §7 模板 |

## 12. 兄弟漏洞挖掘法（sibling bug hunting）★ 实战高收益

> 来源：Node.js 与 Kubernetes 赏金实战（2026-08）。两案皆命中，是源码审计类赏金
> 性价比最高的方法：评审认可度高（有同族先例）、撞车率低于全新挖掘、定级可引用先例。

**核心思路**：以**已知 CVE / 补丁 / 历史报告**为锚点，找**同族但未修复**的兄弟缺陷。

### 方法
1. **建锚点清单**：目标仓库近期 CVE（尤其权限边界/解析类）、安全补丁、历史 triage 报告。
2. **读补丁 diff**：定位修复的函数/逻辑，问三个问题：
   - 修复是否只覆盖了**部分分支**？（Node.js: CVE-2026-58043 修 is_leaf 共享前缀，没修通配符长度判定 `-2`）
   - 修复是否只覆盖了**部分类型/路径**？（K8s: CVE-2024-3177 修 envFrom，漏 projected volume；CVE-2025-5187 修 update 分支，漏 create）
   - 同函数里**对称逻辑**是否一致？（create vs update、A 类型 vs B 类型、通配符 vs 字面）
3. **验证不对称**：对每个"漏掉的兄弟"，写最小复现（本地测试/双版本对照），确认 main 仍受影响。
4. **报告差异化**：明确"与 CVE-xxxx 同类但缺陷不同"，引用先例定级。

### 适用信号
- 权限模型 / 访问控制类边界（allowlist、permission、RBAC、mountable-secrets）
- "缺失类"漏洞（无降级防护/无校验/无参数化）——攻击面固定、根因单一、**独立挖掘撞车率高**，
  用兄弟法+接口枚举+利用链扩展形成差异。

### 案例
| 锚点 CVE | 修复内容 | 挖到的兄弟 |
|---|---|---|
| CVE-2026-58043 (Node.js, High) | radix-tree is_leaf 边界 | 通配符 `-2` 长度判定越权（main 仍存在，双版本实测） |
| CVE-2024-3177 (K8s, Medium) | mountable-secrets envFrom 绕过 | projected volume secret 源漏检（go test 实证） |
| CVE-2025-5187 (K8s) | node UPDATE ownerReferences | node CREATE 分支无限制（修复不对称） |

## 13. 报告工程与证据链（report engineering）★ 从审计到可提交

> 来源：Node.js/Kubernetes 报告流水线实战。目标：**报告完成度 100% 且评审一次看懂、可一键复现**。

### 证据工程（让漏洞无可辩驳）
- **双版本复现**：main/nightly + 稳定 release 线（如 v27 + v26.3.0）各跑一遍。
- **控制组对照**：同一场景的正例/反例（如 `secret*` 越权 vs `secret\*` 正确拦截 ERR_ACCESS_DENIED）。
- **自包含 PoC 包**：一个 `repro.js`/`repro.py` 能自己建场景、跑、出结果（评审直接可跑，跨平台）。
- **固定 commit 引用**：报告行号基于具体 commit，不追 main 前进。
- **真实输出**：报告引用的输出必须当场实跑（main nightly 会过期，提交当日重跑）。

### 报告流水线（五件套）
1. **worklog**：审计全程记录（目标/CVE 对照/发现/排除项/结论）。
2. **report-draft.md**：草稿（9 节：title/weakness/summary/details/repro/impact/fix/severity/attachments）。
3. **report-final.md**：终稿（按 H1 表单字段组织，英文，评审友好，无赏金数字）。
4. **submission-checklist.md**：逐字段映射（Title/CWE/Severity/Description 来源/Impact/附件顺序/兜底）+ open questions（N/K/G 编号）+ 提交步骤 + tracker 模板。
5. **双保险存档**：全文存小脑 + 本地文件，恢复后照清单一键提交。

### 账户限制应对
- 提交受阻（trial 配额/Signal 门槛）→ **先存档全套**，恢复日按清单 30 分钟内完成提交。
- 提交后立即记录报告 ID/URL → tracker + 小脑；dup/关闭 → 复盘原因存经验。

### 报告质量红线（评审一次看懂）
- Title ≤150 字符带 [Program] 前缀点明后果；Summary 30 秒读懂；
- Repro 可复制（命令+真实输出）；Impact 按读/写/API 三面写清；
- **必带 Suggested fix**（部分程序 -25% 修饰符）；Severity 保守下限+引用先例争取。

## 14. 负结果管理与成熟目标评估（避免白费工时）

> 来源：Matomo 5.12 两次负结果实战（静态 XSS 误报、认证边界全健）与 eero 关闭决策。

### 负结果也是结果（同样存工作日志）
- **静态分析结论必须动态验证**：Matomo 静态看出的 0-day XSS 被真实实例 hover 验证推翻
  （输出层 SafeDecodeLabel 统一编码）—— 误报写入记忆，避免下次重复。
- **API 层疑点要下沉到 Repository/core 层核实**：表面上"没防护"的入口可能在下层有统一校验。
- 负结果记录格式：假设 → 验证方法 → 防御层证据 → 结论。防止"感觉还能挖"的沉没成本。

### 成熟项目投入产出比评估（开打前 10 分钟）
- 被全球反复审计的项目（Matomo 等）：认证/输出编码边界通常健全 → 优先找**新引入/少人看**的面
  （新版本差异、周边组件、第三方集成、配置错误）。
- 判断依据：近期安全修复模式（git log security 相关）、CVE 历史密度、程序 scope 与赏金表。
- 无认证外部面（eero 案例）：子域全 ACL 封锁 + 无自助注册 → 该方向直接关闭，
  把时间留给需要账号/更高价值的认证态测试（写清"需用户投入"的路径）。

## 15. 撞车规避与报告生命周期（从提交到关闭）

### Duplicate 规避（TFH #3925369 教训：缺失类漏洞撞车）
- "缺失类"漏洞（无降级防护/无校验/无参数化）攻击面固定、根因单一、教科书式利用 → **独立挖掘撞车率极高**。
- 规避：接口枚举 + 利用链扩展形成差异；发现即提交（**time-to-submit 决定 duplicate 归属**）。
- 被 dup 后：复盘同族已覆盖方向，转向 UART/USB/BLE/JTAG 等未覆盖接口与组件。

### 报告状态机与各态处理
- `New → Triaged → Resolved/Informative/Duplicate`；triage 后关注回复时限（Node.js 5/10 天约定）。
- **Informative 不等于失败**：1Password 案例——理性评估定级合理性，接受后给
  "影响边界确认 + 非阻塞 defense-in-depth 建议"（如 validateMessage 缺 sender 校验），留专业印象。
- **Critical 主菜要盯**：Vercel #3920972（9.2）提交后跟进 triage 反馈，PoC 问题走评论补充
  （提交后正文只读）。

## 16. 多目标管线与账户配额管理（长期作战）

> 来源：2026-08 十五目标并行实战（1Password/Vercel/Matomo/Threema/Uber/Xiaomi/SHEIN/Qualcomm/
> eero/Valve/Temu/TFH/Node.js/Kubernetes + Hacker101）。

### 多目标并行流水线
1. **候选筛选**：程序清单 × 高价值×低竞争打分（赏金表/竞争度/技能匹配/响应效率）。
2. **批量 Intake**：多个目标并行做政策/scope 研究（Playwright 抓页），各存小脑。
3. **按技能分组派发**：Web 面/源码审计（DEEPCODE 子代理）与固件/二进制面（逆向工具链）并行。
4. **报告就绪队列**：完成的报告进队列（本地+小脑双保险），等账户条件允许再提交。
5. **目标关闭判据**（三选一即关）：无攻击面/负结果穷尽/发现已报完 → 记录"为何关闭"防回锅。

### 账户配额与资格管理（H1 核心机制）
- **trial 配额**：新账户按 30 天滚动恢复；1Password/Vercel/TFH 三个报告即耗尽 → 后续被硬拦。
- **Signal**："Still being determined" 直到有报告 Triaged/Resolved；无 Signal Requirement 的程序是突破口。
- **私密邀请**：Hacker101 CTF 26 分解锁（你已 276 分 ✓）—— 私密程序竞争小、通常越过 trial 门槛。
- **提交受阻应对**：报告全套存档（终稿+清单+附件）→ 恢复日 30 分钟一键提交（Uber 30 天 pending 模式）。
- **禁止绕过**：串通/OOB/无关评论会加重限制（H1 明示）。
- 外部平台（GObugfree 等）注意身份验证流程（Threema 案例：身份证 OCR 失败 → 官方邮件通道）。

## 15. 逆向理解资源 (Build-Your-Own-X) ★ 理解力补药

> 完整地图：知识库 `byox-逆向理解资源地图-2026-08-23`（vault 已语义索引，搜 "byox 教程" 可召回）。
> 来源：codecrafters-io/build-your-own-x（CC0 教程索引）。Feynman："What I cannot create, I do not understand" —— **逆向一个东西前先知道它怎么构建，理解速度翻倍**。

| 逆向/自研场景 | BYOX 类目 | 代表教程 |
|---|---|---|
| DSL/VM/混淆（dsl-vm-reverse、starlark 移植） | Programming Language | Crafting Interpreters / mal / Super Tiny Compiler |
| 风控正则/WAF 规则（js-reverse） | Regex Engine | swtch "RE Matching Can Be Simple And Fast" |
| 记忆检索/向量（cerebellum） | Search Engine | Vector Space Indexing / TF-IDF / feedback 回灌 |
| 存储/协议（sqlite/redis、RESP 逆向） | Database | B+Tree / Build Your Own Redis |
| shell/webserver 底层（DSH 排查） | Shell / Web Server | Write a Shell in C / BYO Web Server (Node) |
| 网络协议（protocol-reverse） | Network Stack / DNS / MQTT | TCP/IP stack / DNS guide / Sol MQTT broker |
| 固件/字节码（firmware/malware） | Emulator / VM | LC3 VM / CHIP-8 / bytecode interpreters |
| LLM/RAG（cerebellum/ensemble） | AI Model | LLMs-from-scratch / rag-from-scratch |

**用法**：
1. 逆向前先查对应类目 15 分钟，再碰目标二进制/协议；
2. 自研组件前用教程做骨架（如 apply-patch、记忆索引）；
3. 让 agent 干活前注入本表，agent 知道去哪找参考实现。

**维护**：每半年对一遍 [BYOX README](https://github.com/codecrafters-io/build-your-own-x) 看新增对口类目；落地新移植后在对应行补"已落地"。

## 17. 报告失败模式复盘（★ 2026-08 七报告全败实战）

> 来源：2026-08-06 ~ 08-19 七份 HackerOne 报告全部未获 bounty（4 Dup + 2 Informative + 1 待评估）。
> 核心事实：**报告做得好 ≠ 能拿钱**；撞车（Duplicate）和影响论证（Informative）是两大杀手。

### 17.1 七报告结果清单（raymondginger）

| # | 程序 | 漏洞类型 | 结果 | 失败主因 |
|---|---|---|---|---|
| 3934988 | Wolt | CORS 任意 Origin 反射 | Duplicate | **撞车晚 3.5h**（正确 dup→#3934644）|
| 3934963 | Wolt | 未认证 receipts 下载（遗留端点）| Duplicate | **撞车晚 2 年**（原始 2024-06-23）|
| 3934930 | Wolt | 未认证 corporate-leads 写入 | New(Open) | 待评估；注意同族无认证 |
| 3925369 | TFH | orb-firmware DFU 降级 | Duplicate | **撞车晚 3 个月**（原始 5/11，High）|
| 3920972 | Vercel | AI SDK MCP OAuth redirect | Informative+上诉 | **信任边界论证**（需恶意服务器）|
| 3920395 | 1Password | WebAuthn postMessage 校验 | Informative | 信任边界 + 自同意（专业收尾）|
| 3953711 | AIG | CAPTCHA 未校验 | Informative | **无实际数据泄露演示**（先例 3668265）|

### 17.2 失败根因分类（从 7 份提炼）

**A. 撞车类（Duplicate）—— 4/7，占比最高**
- CORS 反射/遗留端点/固件降级 = 攻击面固定、根因单一、教科书式 → **独立挖掘撞车率极高**。
- 关键：**time-to-submit 决定归属**。同一天撞车（晚 3.5h）与晚 2 年/3 月，本质都是"别人先报了"。
- 观察：Wolt 3 份全扑（CORS/receipts/leads），说明成熟程序高竞争池子已被反复筛过。
- 反例参照 §12 兄弟漏洞挖掘法：只有**不同缺陷**（如 58043 的 -2 判定 vs is_leaf）才不撞车。

**B. 影响论证不足类（Informative）—— 3/7**
- **"信任边界"拒绝模式**（SDK/扩展类）：triage 惯用答复 = "需连接恶意/被攻陷服务器（或页面已注入 JS）= 信任边界违反 = out-of-scope"。
  - 这是 Vercel + 1Password 的拒绝理由。应对：必须**在初始报告里**就写好"不依赖恶意服务器"的场景
    （合法服务器 open redirect → 凭据外带；跨网络边界 SSRF），不要等 Informative 才补。
  - 1Password 自我接受 + 留非阻塞建议 = 专业收尾（见 §15），保印象分。
- **"无实际泄露"拒绝模式**（认证绕过类）：AIG CAPTCHA 教训 = 没有有效保单+ID 返回数据的演示，
  triage 一律判"无显著安全影响"。应对：**先拿到能证明出受保护数据的真实组合**再提交。

### 17.3 行动修正（下次使用）
1. **撞车类漏洞**（CORS/端点/降级）：先查小脑历史报告清单（避免已报方向）→ 发现即瞬间提交。
2. **SDK/扩展类**：报告正文**第一个攻击场景必须不依赖恶意服务器**（open redirect/普通功能链）。 
3. **认证绕过类**：没有"能读到受保护数据"的实证，不提交——负结果先排查可测数据面。
4. **跟进入口**：Vercel 上诉 19 天未回 → 30 天时礼貌跟进；AIG/Wolt 已关的不再纠缠。
5. **配额管理**：Trial 已耗尽（2026-09 恢复），这轮 7 份全败 + 零 Signal → 恢复后优先提交
   Node/K8s/Uber SSRF 三份已就绪的 med+ 报告（§16 清单）。

## 18. GitHub 素材增强库（★ 2026-08-26 调研整合）

> 来源：subagent 深度调研 17 个 GitHub 仓库（`worklogs/github-material-survey.md`）。
> 目的：把开源社区已验证的素材直接接入 SKILL，解决撞车规避、影响论证、报告质量三大痛点。

### 18.1 P0 立即接入（直接提升下一份报告结果）

**① h1-brain MCP 服务器**（PatrikFehrenbach/h1-brain，~100+⭐）★ 最大价值
- 内置 **3600+ 已披露赏金报告** SQLite 库（`disclosed_reports.db`，零配置随仓库发放）
- 支持同步个人 H1 历史（报告/程序/scope/附件）+ 全文搜索公开报告
- **`hack(handle)` 一键攻击简报**：fresh scope + 个人历史 + 弱电模式 + 未触及资产 + 攻击向量建议
- **用法**：注册为 DSH MCP 工具（`server.py`，H1 API token）；提交前 `search_disclosed_reports` 查同程序/同弱电避免撞车；对目标跑 `hack()` 用"在其他程序已支付但此处未发现"的弱点做兄弟挖掘锚点。
- 落地：`pip install -r requirements.txt` → 配置 `H1_USERNAME`/`H1_API_TOKEN` → 加入 DSH MCP 配置。

**② 报告模板增强**（ZephrFish/BugBountyTemplates，499⭐）
- 6 种模板：`Blank.md`（完整版含写作指南）/`HeadersOnly.md`（快速骨架）/`short.md`（简洁版）/`SSRF.md`/`API.md` 专用
- 含 **CVSS 3.1 快速评分表** + **证据收集 8 项清单**（截图 URL 栏/时间戳/唯一标识符）+ **常见错误清单**
- **用法**：把 4 个模板复制到 `worklogs/templates/`；SSRF/API 报告直接用专用模板（本次 Uber SSRF 适用）。
- 常见错误清单正是 §17.2 撞车/Informative 的预防对照表。

**③ 现代漏洞高级技术**（gl0bal01/intel-codex `sop-bug-bounty.md`）
- SKILL §4 缺的高级技术章节：**HTTP Smuggling、Cache Deception/Poisoning、Prototype Pollution、JWT、SSTI、XS-Leaks、OAuth/SAML、Race Condition（single-packet attack）、Price Manipulation、Mass Assignment、GraphQL**
- 含 Pre-Engagement 8 项授权检查（re-pull scope on test day / disclose.io 4 元素 / VDP vs BBP / scope-creep guardrail）
- **用法**：把高级技术清单并入 §4 模式库；Pre-Engagement 并入 §1 资格筛查。

### 18.2 P1 本周接入（中期提升）

| 素材 | 仓库 | 价值 |
|------|------|------|
| **Top 100 已披露报告**（28 类弱电 + 50+ 程序）| reddelexc/hackerone-reports（6,477⭐）| 定级先例引用 + 兄弟挖掘锚点；报告定级时引用同类 Top 报告 |
| **7-Question Gate 提交前自检** | elementalsouls/Claude-BugHunter（681 已披露模式）| 提交前 7 问（scope/影响/evidence 完整性），减少 Informative |
| **36,181 条场景 playbook**（47 类弱电 + 攻击决策图 + 17 CVE 链）| AlaBYahya/pwn-scenarios | 兄弟挖掘状态机：`idor_confirmed → unauthorized_privileged_action_possible` 链 |
| **400+ 单行命令**侦察方法论 | KingOfBugbounty/KingOfBugBountyTips（5,520⭐）| §3 侦察命令集丰富（子域/JS/SSRF/API/Cloud）|

### 18.3 P2 有空接入（锦上添花）

- **VulnDraft**（ruyynn）：报告生成器 + CVSS v3.1 计算器（CLI/Web）
- **bountyplz**（fransr，463⭐）：Markdown → 自动提交 H1/Bugcrowd（含附件/2FA/严重级）
- **awesome-csirt**（Spacial）：CSIRT 资源（偏蓝队，KingOfBugbounty 链接索引）
- **Vulpine-Security/Security-Research**：赏金发现复盘（好/坏发现方法论）
- **DevCop95/bugbounty-lab101**：完整赏金工作空间（范围执行 + 工具管线 + 本地 VM 练习）
- **xalgord/Massive-Notes**：13 阶段 Web 渗透教程（视频为主）

### 18.4 调用时机（与 §12/§15/§7 衔接）

1. **选目标** → §1 资格筛查 + gl0bal01 Pre-Engagement 8 项检查
2. **撞车规避** → 提交前用 h1-brain `search_disclosed_reports(program, weakness)` + 查个人历史 → 发现即瞬间提交（§15 规则）
3. **兄弟挖掘** → h1-brain `hack()` 识别"未触及资产 + 弱点模式" + pwn-scenarios 攻击决策图 → §12
4. **报告写作** → ZephrFish 模板（SSRF/API 专用） + 证据清单 → §7
5. **定级论证** → reddelexc Top 报告同类先例引用 → §7-Severity

### 18.5 维护
- 每季度用 GitHub API 搜 `bug bounty` / `hackerone` 看新增高星仓库
- 落地新工具后在对应行补"已接入"

## 19. Epic Games VDP 实战经验（★ 2026-08-28 双账号全流程）

> 来源：Epic Games VDP 完整侦察（无认证 → 注册测试账号 → 认证态 IDOR 测试 → 双账号）。
> 结论：**防御到位，无认证态可提交漏洞**。以下是可复用的方法论（对任何需要测试账号的大厂 VDP 适用）。

### 19.1 测试账号注册（含 sectest 要求）
- Epic 显示名规则：`字母数字下划线连字符句点`（sectest 必须出现在用户名，H1 程序要求）
- 出生日期：用 1990-01-01（≥18 岁），month 是 **MUI Autocomplete**（点开下拉选"一月"，不能直接 fill）
- 邮箱：**Epic 拒绝 Gmail `+` 别名和点号别名**（`invalid_email`），需独立邮箱；邮箱验证码 30 分钟有效
- MFA 登录：验证码邮件主题"你的双重登入代码"，发到注册邮箱；后续会要求确认 MFA 方式（点"确认"）→ 关联主机（点"关联完成"跳过）
- 双账号：两个独立邮箱（Gmail + QQ），显示名 A=`xxx-sectest` / B=`xxx-sectest-2`

### 19.2 认证态会话获取（关键技术）
- **EPIC_EG1 cookie** = OAuth access token（格式 `eg1~JWT`，2923 字符），存于 `.store.epicgames.com`
- **REFRESH_EPIC_EG1** = refresh token（同样 `eg1~` 前缀）
- 导出会话：`page.context().storageState({path})` → 完整 cookie + localStorage
- Python 复现：httpx client 带 `Authorization: Bearer <EPIC_EG1>` + 完整 cookie jar + `X-Bug-Bounty` 头
- ⚠️ token 约 8 小时过期（storeTokenExpires），会话需定期刷新

### 19.3 Epic 私有 API 面（egs-platform-service.store.epicgames.com）
| 端点 | 说明 |
|---|---|
| `/api/v2/private/account/wallet/balance` | 钱包余额（Bearer token 身份）|
| `/api/v2/private/egs/gifting/gifts?type=sent/received` | 礼物列表 |
| `/api/v2/private/egs/gifting/gifts/count` | 礼物计数 |
| `/api/v2/public/egs/gifting/offers/giftable` | 可送礼商品 |

**认证要点**：
- 身份来自 **Bearer token**，**不信任客户端传入的 accountId**（交叉注入返回自己的数据 → IDOR 防护到位）
- gifting 创建走商店结算流（非直接 API），无法轻量生成 gift ID
- 浏览器内 fetch 跨域失败（CORS），需 httpx/page.request 带 token

### 19.4 store GraphQL（store.epicgames.com/graphql）
- **introspection 禁用**（Apollo Server 生产配置）
- **非持久化查询可执行**（`{__typename}`→200）— 攻击面真实
- 错误信息泄露字段结构（`Unknown argument on field "CatalogQuery.searchStore"`）→ 可字段枚举
- 只有 **Catalog 公共数据面**（searchStore 商品搜索），**无用户数据面**（Me/Wallet/gifts 字段不存在）→ 无 IDOR 价值

### 19.5 方法论沉淀（对同类大厂 VDP）
1. **先无认证侦察**（JS bundle/GraphQL/CORS/子域）→ 确认防御基线
2. **注册测试账号**（sectest 命名）→ 走完整认证流程
3. **导出会话**（storageState + EPIC_EG1 token）→ Python 脚本系统化测试
4. **对象级授权验证**：交叉注入 accountId / 对象 ID → 接口是否信任客户端输入
5. **诚实关闭判据**：防御到位 → 标记关闭，记录"为何关闭"防回锅（§16）
6. **投入产出比评估**：需真实商店购买才能测试的面（如 gifting 创建）→ 边际收益低，及时止损

### 19.6 本次未完成/可续接
- gifting IDOR 完整链路（需真实送礼生成 gift ID 后跨账号读）— 投入产出比低，暂缓
- Epic OAuth 全流程 redirect_uri 校验（登录态验证）— 需在认证流中构造恶意 redirect 参数
- Fortnite/UEFN 客户端二进制面 — 未做静态分析（技能匹配但需下载客户端）


## 19b. Pantheon GraphQL 测试实战（★ 2026-08-31）

> 来源：Pantheon (Bugcrowd) Bug Bounty 实战。目标：`https://dashboard.pantheon.io/graphql`

### 攻击向量：GraphQL Schema Enumeration via Error Messages

**原理**：当发送无效 GraphQL 查询时，服务器的错误消息泄露完整 schema 结构（"Did you mean" 建议 + 字段验证错误）。

**枚举方法**：
1. 查询不存在的字段 → 泄露可用字段名
   ```graphql
   { sites { id } }  → "Did you mean 'site' or 'siteCDN'?"
   ```
2. 查询错误的参数类型 → 泄露参数要求
   ```graphql
   { user { id } }  → "Field 'user' argument 'id' of type 'ID!' is required"
   ```
3. 查询不存在的 mutation → 泄露可用 mutation
   ```graphql
   mutation { createSite }  → "Did you mean 'deleteSite'?"
   ```

**泄露的完整 schema**：
- Query: `user(id:ID!)`, `site(id:ID!)`, `siteCDN`, `organization(id:ID!)`, `currentUser`, `environments(siteId:ID!)`, `workspace(id:ID!)`
- Mutation: `deleteSite`, `createWorkspace`, `deleteUser`, `updateUserEmail`, `updateSitePlan`
- Types: `User`(id/name/email/machineTokens), `Site`(id/label/plan), `Plan`(object)

**额外发现：Schema Hash 泄露**：
```
响应头: schemahash: 63c06bb3e206b0759d6e3d38c1df9a88d79f8195a8e9943ef72964be504af027
```
攻击者可用此哈希值追踪 schema 变化、检测安全补丁。

**修复建议**：
1. 返回通用错误，不提供 "Did you mean" 建议
2. 从响应头中移除 schemahash
3. 禁用 GraphQL 验证提示

**适用场景**：任何禁用 introspection 但保留错误提示的 GraphQL API。

## 19c. Bugcrowd 提交限制规则（★ 2026-08-31 实战）

> 来源：Bugcrowd 官方文档 + BitGo/Pantheon 提交实战。

### MBB (Managed Bug Bounty) 限制
- **并发限制**：同时最多 **5 个** pending 提交（New/Triaged 状态）
- **VDP 不受限**：VDP 提交不计入限制
- **释放机制**：提交状态变为非 Open（Resolved/Informational）→ 立即释放位
- **豁免条件**：有优质提交记录的账户可被豁免

### 提交策略
1. **MBB 提交高质量报告**（少而精，每个都值得）
2. **VDP 不受限** → 可批量提交练手
3. **提升账户表现** → 优质记录 → 被豁免限制
4. **等 Triage 处理** → 释放更多提交位

### VRT 选择技巧（Bugcrowd React 组件）
- VRT Category 是**自定义 React listbox**，不是标准 `<select>`
- `selectOption()` **只适用于 `<select>` 元素**，对 React 自定义组件无效
- **必须通过 DOM listbox 点击选择**

**正确操作方法（Playwright 原生 API，2026-08-31 实测成功）**：
```typescript
// ⚠️ VRT 是二级结构：父级 > 子级

// 步骤 1: 点击 VRT 触发区域打开下拉
page.locator('#vrt-form-input').locator('..').getByRole('button').click();

// 步骤 2: 等待 listbox 出现
page.getByRole('listbox').waitFor();

// 步骤 3: 点击父级选项（如 "Sensitive Data Exposure"）
page.getByRole('option', { name: 'Sensitive Data Exposure' }).click();

// 步骤 4: ⚠️ 等待子级菜单出现，再点击子级选项
page.getByRole('option', { name: 'Disclosure of Known Public' }).click();

// 步骤 5: 验证选择成功（VRT 框显示 "父级 > 子级"）
// 如："Sensitive Data Exposure > Disclosure of Known Public Information"
```

**关键教训（2026-08-31 实测）**：
- ❌ 只选父级不选子级 → VRT 不生效 → Save draft disabled
- ✅ 必须选完父级 + 子级 → VRT 才生效
- Playwright `selectOption()` 只适用于 `<select>`，对 React 自定义组件无效
- 正确方法：`getByRole('listbox')` + `getByRole('option')` + 两级点击

**VRT 分类结构（部分）**：
| 父级 | 子级 |
|------|------|
| Sensitive Data Exposure | Disclosure of Known Public Information |
| Sensitive Data Exposure | Disclosure of Secrets |
| Broken Access Control (BAC) | ... |
| Server-Side Injection | ... |
| ... | ... |

## 19d. MATLAB Online Bug Bounty 实战（★ 2026-08-31）

> 来源：MATLAB Online (Bugcrowd) 实战。程序仅 264 人参与，极低竞争。
> 目标：https://matlab.mathworks.com/

### 发现清单（4 份报告已提交）

| # | 漏洞 | 严重级别 | 状态 |
|---|------|---------|------|
| 1 | config.json 公开可访问（内部端点+功能开关） | P3 | ✅ 已提交 |
| 2 | llms.txt API 文档泄露（完整端点文档） | P3 | ✅ 已提交 |
| 3 | CSP unsafe-inline + unsafe-eval | P4 | ✅ 已提交 |
| 4 | authnz 端点泄露完整用户身份+90天认证 token | P1-P2 | ✅ 已提交（含 PoC） |

### 关键攻击技术

**mwtype 协议分析**（MATLAB Online 内部 RPC 协议）：
- 格式：`{"mwtype": "namespace/ActionName", ...params}`
- 从页面网络请求中可以捕获正确的 mwtype 值
- authnz 端点：`{"mwtype": "authnz/ContextTokenCheck"}` 返回完整用户信息
- artifacts 端点：`{"mwtype": "mwartifacts/Query", ...}` 查询文件列表

**SSRF + mwtype 组合**：
- 如果存在 SSRF，可以调用 authnz 端点获取任意用户的认证 token
- mwajToken 有效期 90 天，可长期访问用户资源
- 认证信息包含：subjectId, email, firstName, lastName, profilePicture

### 启发（适用于其他目标）

1. **网络请求捕获**：登录后用浏览器 DevTools 捕获页面加载时的 API 请求，找到正确的 mwtype/GraphQL/REST 格式
2. **公开配置文件**：`/config.json` 是常见信息泄露源（React/Vue 应用）
3. **AI/LLM 集成文档**：`/llms.txt` 是新兴的信息泄露目标（为 LLM 提供的 API 文档）
4. **CSP 弱点分析**：`unsafe-inline` + `unsafe-eval` = XSS 无防护

### Bugcrowd 提交流程（本次实战）
- VRT Category 通过 **listbox 点击选择**（不是 select/fill）
- 附件只支持 **.jpg/.gif/.png**，不支持 .sh
- Save draft 按钮在 VRT 未选时 disabled
- "Approaching submission limit" 警告 = 接近 5 个 pending 限制

## 20. Hacker101 CTF 剩余挑战攻克技能（★ 2026-09-01 学习沉淀）

> 来源：GitHub 调研 + 系统学习 + 实战笔记（phase1-web-security-study.md / phase2-crypto-study.md / phase3-binary-study.md）。
> 目标：攻克 Encrypted Pastebin (Hard, 4 flags)、Rend Asunder (Expert, 3/3 flags)、Grayhatcon CTF (4 flags)。

### 20.1 Grayhatcon CTF 攻克（Moderate, 4 flags, 1-2h）

**漏洞链**（按攻克顺序）：

| Flag | 类型 | 攻击方法 | 关键 payload |
|------|------|---------|-------------|
| Flag 2 | IP Spoofing | X-Forwarded-For 绕过 .htaccess IP 白名单 | `X-Forwarded-For: 8.8.8.8` |
| Flag 1 | 输入验证 | 注册表单注入 owner_hash 参数（Mass Assignment） | `owner_hash=<hunter2_hash>&new_username=x&new_password=y` |
| Flag 3 | IDOR | 替换 Set-Cookie 中 userhash 为 hunter2 的 hash | Burp 修改 Set-Cookie 响应 |
| Flag 4 | SQLi Inception | `/question?id=` 双层嵌套 SQLi（JSON body 中的 SQL） | `0 union select '0 union select username...from admin',2,'[]'--` |

**关键技能**：
- Burp Match & Replace 自动添加 X-Forwarded-For 头
- ffuf 目录爆破找 .htaccess
- SQLi in JSON（UNION 注入 + 嵌套 SQL 读 information_schema）
- Cookie 篡改 + Set-Cookie 替换

**学习资源**：[krash.dev writeup](https://krash.dev/posts/bug-bounty-summit-ctf-writeup/)、PortSwigger Academy

### 20.2 Encrypted Pastebin 攻克（Hard, 4 flags, 2-3 天）

**漏洞链**：

| Flag | 类型 | 攻击方法 |
|------|------|---------|
| Flag 0 | 信息泄露 | traceback 泄露服务器路径/版本 |
| Flag 1 | Padding Oracle | AES-CBC Padding Oracle 解密密文 |
| Flag 2 | CBC 伪造 + IDOR | 利用 Oracle 伪造密文 + 跨用户 IDOR |
| Flag 3 | SQLi + Forge | 在 Oracle 解密的明文中发现 SQL 注入点 + 密钥伪造 |

**核心技能 — Padding Oracle Attack**：
```
原理：服务器对"填充无效"和"填充有效"返回不同响应 → 逐字节推断明文
关键：PKCS#7 填充验证逻辑（最后一个字节值 = N，最后 N 个字节都是 N）
PoC：遍历最后一个字节（0-255），找到唯一一个返回不同响应的值 → 推断明文
```

**Python PoC 框架**：
```python
def padding_oracle_attack(ciphertext, iv, oracle_url):
    blocks = [iv] + [ciphertext[i:i+16] for i in range(0, len(ciphertext), 16)]
    decrypted = b""
    for block_idx in range(1, len(blocks)):
        intermediate = bytearray(16)
        for byte_idx in reversed(range(16)):
            for guess in range(256):
                intermediate[byte_idx] = guess
                crafted = bytes([intermediate[i] ^ blocks[block_idx-1][i] for i in range(16)])
                if oracle(crafted + blocks[block_idx]):
                    # 找到有效填充
                    break
        decrypted = bytes([intermediate[i] ^ blocks[block_idx-1][i] for i in range(16)])
    return decrypted
```

**自定义 Base64 变种**：Encrypted Pastebin 用 `+→-`, `/→!`, `=~` 变体

**学习资源**：[Cryptopals Ch17](https://cryptopals.com/sets/2/challenges/17)、[beny23 教程](https://beny23.github.io/posts/capturing_a_padding_oracle/)、[eggburg 自动化脚本](https://github.com/eggburg/hacker101_CTF_Encrypted_Pastebin)

### 20.3 Rend Asunder 攻克（Expert, 3 flags — 已全清 ✅）

**漏洞类型**：Native — JavaScript 脚本编辑器 + 服务端 HeadlessChrome 渲染

**架构**：用户提交 JS → 服务端 HeadlessChrome/67 执行 → 截图返回 PNG

**3 Flag 获取方法**（已实战验证）：

| Flag | 攻击方式 | Payload |
|------|---------|---------|
| Flag0 | alert 触发特殊 GIF | `alert(1)` |
| Flag1 | DOM 遍历读取 noscript | `document.write(Array.from(document.querySelectorAll('*')).map(e=>e.tagName+':'+e.textContent.substring(0,100)).join('\n'))` |
| Flag2 | SSRF 到内部 Flask 端点 | `fetch('http://localhost/f37c09551527e414ecbbab3a213e1f73').then(r=>r.text()).then(t=>{document.body.innerText='RESP:'+t.substring(0,2000)+':END'})` |

**核心洞察**：
- HeadlessChrome 在服务端执行，fetch() 从服务器发起 → SSRF 到 localhost
- Flag 隐藏在 `<noscript>` 标签中（JS 启用时不可见但 DOM 可读）
- 127.0.0.1 被 CORS 阻止，但 localhost 端口 80 可达

**攻击模式**：无头浏览器渲染 + SSRF（类似 BugDB v1 模式）

**学习资源**：[ROP Emporium](https://ropemporium.com/)、[pwn.college](https://pwn.college/)、[CTF Wiki Binary](https://ctf-wiki.org/pwn/linux/user-mode/stack-intro/)

### 20.4 深入安全知识沉淀（★ 2026-09-01）

**SSRF 攻击链**：
- HeadlessChrome/67 + nginx + Flask 架构下的 SSRF 路径
- `localhost` 允许但 `127.0.0.1` 被 CORS 阻止（浏览器同源策略特殊处理）
- 服务端 JS 执行 → `fetch()` 从服务器发起 → 访问内部端点
- 关键端点：`/proc/self/environ`、`/proc/1/cmdline`、Flask debug 页面

**格式化字符串漏洞**：
- `%p`/`%x` 泄露栈数据 → `%n` 写入内存
- 绕过 Canary/PIE/ASLR
- 自动化工具：pwntools `fmtstr_payload`

**堆利用基础**：
- tcache poisoning / fastbin attack / UAF / House of Botcake
- glibc 2.23-2.34 版本差异
- Safe-linking 绕过（glibc 2.32+）

**HeadlessChrome 沙箱逃逸**：
- file:// 协议在 HeadlessChrome 中通常可通过 CDP 访问
- `/proc/self/environ` 环境变量读取
- SSTI RCE：`config.__class__.__init__.__globals__['os'].popen()`

**详细笔记文件**（共 15+ 个文件）：
- `worklogs/phase1-web-security-study.md`（700 行）
- `worklogs/phase2-crypto-study.md`（776 行）
- `worklogs/phase3-binary-study.md`（682 行）
- `worklogs/phase3-module1-elf-gdb.md`
- `worklogs/phase3-module2-buffer-overflow.md`
- `worklogs/phase3-module3-rop.md` + 7 个附属文件
- `worklogs/phase3-deep-ssrf-sandbox-study.md`
- `worklogs/phase3-deep-memory-study.md`
- `worklogs/rend-asunder-ssrf-test.md`
- `worklogs/rend-asunder-flag2-test.md`
- `rop-exploit-template.py` / `rop-emporium-practice.py` / `rop-gdb-script.py`

**维护**：实战攻克后更新 §3 漏洞模式库 + §16 覆盖核对表。

