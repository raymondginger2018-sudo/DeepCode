---
name: bug-bounty-omnibus
description: >
  漏洞赏金全能总纲（2026-08 整合沉淀）。融合 DEEPCODE 侧（hacker101-ctf-farming 漏洞模式库、
  zcode-re-analysis 逆向工具链、deepcode-cerebellum 记忆引擎、deepcode-agent-sdk）与 HARNESS 侧
  （dsh-code-review 代码审计、record-browser-gif PoC 录制）的精华，加上赏金专属流程：
  资格筛查、目标研究、合规红线、报告提交、triage 沟通、子代理协作。
  适用于：HackerOne / Bugcrowd / SRC 赏金任务全流程（选目标 → 研究 → 侦察 → 挖洞 → 验证 → 报告 → 提交 → 跟进）。
whenToUse: >
  用户要求"做赏金/挖洞/审计某目标"、"选下一个赏金目标"、"提交报告"、"研究某程序"、
  "总结赏金经验"、或任何涉及授权安全测试任务时使用。本 SKILL 是赏金任务的总纲，
  具体打法可再引用 hacker101-ctf-farming 的 §3 漏洞模式库。
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

## 4. 漏洞模式库（直接引用 hacker101-ctf-farming §3）

- **Web**：IDOR / SQLi（UNION/报错优先）/ XSS / SSTI（{{template:...}}）/ XXE / 逻辑缺陷
  （价格篡改/科学计数法/状态机）/ 无认证管理页 / LFI→RCE（http: 绕过+二次解析）/
  自定义请求头鉴权（密钥头泄露）/ 任意上传（扩展名+路径穿越→RCE）。
- **API/GraphQL**：introspection → node()/find* 全局 ID 越权（base64 类型:ID）、对象类型差异、
  mutation 匿名可写、字段级授权缺失、跨版本 X-Token/X-Session、SSRF（avatar/URL 字段 → 内网）、
  目录遍历（../..%5c 混用反斜杠）、隐藏参数（?verbose）、Zip Slip、.htaccess 覆盖。
- **Android APK**：静态逆向（硬编码密钥/flag、HMAC 签名、WebView JS 桥、深链、cleartext）。
- **Crypto/Math**：CBC padding oracle（错误页差异）、IV 翻转、自定义 base64、滚动码
  （LCG/xorshift/LFSR，GF(2) 用 BM/高斯消元）、salt+md5 预测、0.0.0.0 绕过 localhost 检测。
- **Recon**：robots/注释/混淆 JS/隐藏 App IDOR/备份文件。

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

### 提交
- 平台私密提交；正文不含赏金数字/个人信息；附件按程序偏好（.js/.go 受限时贴正文）。
- 提交后**立即记录报告 ID + URL** 到 tracker（worklogs/h1-reports-tracker.md + 小脑）。

### triage 沟通
- 响应及时、专业；被要求补充时按需给证据；dup/关闭时复盘原因记入小脑经验。
- 引用同类先例（CVE 编号/历史报告）争取合理定级。

## 8. 工作流自动化

### 子代理协作（强制模式）
- 分工：子代理做侦察/审计/复现并写工作记录；父代理负责平台操作（提交/账户）与最终判断。
- prompt 必带：目标+范围红线、节流守则、已入账/已知信息清单（避免重复）、工作记录路径、结构化交付格式。
- 后台通道最稳；子代理滞后汇报 → 以第一手证据（check_flag 响应/分数/请求响应）为准回复。

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

| 阶段 | 覆盖 |
|---|---|
| 资格筛查/Intake | §1-2 |
| 侦察/信息收集 | §3 |
| 漏洞模式 | §4（7 类全） |
| 逆向/审计/浏览器 | §5 |
| 验证/PoC | §6 |
| 报告/提交/triage | §7 |
| 自动化（子代理/小脑/SDK） | §8 |
| 合规红线 | §0 |

**经验法则**：新漏洞类型/新平台机制/新教训 → 更新对应章节；保持 SKILL 自我进化。

## 11. 相关技能索引（DEEPCODE 主库 `.deepcode/skills/`，114 个中赏金相关 40+）

> 全能总纲管流程与合规，下面这些是**按场景调用的专业 skill**（DEEPCODE 自带，直接按名加载）。

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



