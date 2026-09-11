---
name: hkuds-pr
description: >
  向 HKUDS/DeepCode 上游贡献 PR 的过审工作流 —— 小 PR 拆分、维护者评审偏好、
  CI 四线自检、红 CI 诊断、flaky 测试目录、评审留言礼仪。
  Use when opening/fixing/following up PRs to HKUDS/DeepCode, when a HKUDS PR
  CI goes red, when responding to Zongwei9888's review, or when planning the
  next DeepCode upstream contribution.
version: 1.7.3
extends: deepcode-coach  # 可被教练循环调用，也继承教练的诊断方法
author: raymondginger
date: 2026-09-10
tags: [hkuds, deepcode, pr, ci, review, contribution]
---

# HKUDS/DeepCode PR 过审工作流

目标仓库 `HKUDS/DeepCode`，fork `raymondginger2018-sudo/DeepCode`。
评审人：Zongwei Li（@Zongwei9888）。全部规则来自真金白银的教训。

## 安全变更审批流程（P0 审计修复）

涉及 **安全、权限、沙箱、认证** 的 PR 必须：
1. 添加 `security` label
2. 显式请求 @Zongwei9888 评审
3. 在 PR 描述中附加 **Security Considerations** 章节：
   - 改变了哪些攻击面
   - 为什么是安全的（fail-safe 默认）
   - 做了哪些安全验证测试
4. 未经安全评审不得合并

> 这一规则已在 `F:\DS-HARNESS\hkuds-ci\DeepCode\CONTRIBUTING.md` 中写明，新建 PR 时应确保其引用。

## 铁律（违反过就会付出代价）

1. **一个 PR = 一个模块/一个意图**。#181（8 模块 +3031 行）换来维护者原话：
   拆成独立 PR "我们可以更快逐个合入，你也不必等最慢的那一个"。
   超过 ~1000 行或跨模块 → 先拆再提。
2. **试合验证**：维护者每次评审都在最新 main 上试合 + 全量测试 + ruff。
   推送前本地必须做过同一件事（rebase 最新 upstream/main → 全量绿）。
3. **新文件 + 环境开关 优先**：纯新增文件、行为用 env knob 门控且默认关闭
   （如 `DEEPCODE_KEYRING`）是最易合入的形态，被明确表扬过
   （"边界处理得很好，对 review 和回滚都友好"）。行为修改单独成 PR。
4. **fail-safe**：平台/安全/权限类改动必须"失败时仍可用"。
   #148 合入后破坏 main 被 revert，维护者反馈 "The idea is right,
   ordering just needs to be fail safe" → 按此重做的 #164 才有资格回来。
5. **先落地本地再提 PR**（已有小脑铁律 `rule:improvements-land-locally-first`）。
6. **维护者修复版优先**：当 rebase 到包含维护者侧修复的 main（如 #192 整合 PR）时，
   冲突文件一律以上游终版为准，不能把本地旧实现带回去。
   2026-08-25 案例：runtime.py 的 `_register_server_tools`、memory.py 的 scoped 排除逻辑
   均已被维护者改写，rebase 时保留上游版本。
7. **rebase 到最新 upstream/main 再推进**：只要上游 main 前进了，PR 分支应先 rebase
   再提交/请求评审。2026-08-29 案例：`pr/genai-p1` 落后 upstream/main 9 个 commit，
   rebase 零冲突完成（13 个 commit 干净重放），CI 自动触发并 14/14 全绿。
8. **Fork PR 的 CI 卡 `action_required` 不是 workflow 问题**：上游仓库若开启
   "require approval for first-time fork PR Actions"，所有 fork PR 的 workflow 都会停在
   `action_required`（页面显示 "1 workflow awaiting approval"，Checks 计数 0）。
   这不是 workflow 文件缺/坏，无法从 fork 侧修复 → 唯一路径是 PR 留言 @ 维护者请求审批。
   2026-08-29 案例：lessweb/deepcode-cli 的 #267/#298/#299/#301 全部如此。
9. **CI 守望用浏览器直接看（Playwright）**：`checks` 页面看总进度，点进具体 job 看步骤。
   Bundle/桌面构建是最慢 job（macOS x64 可达 17m+），纯 Python 改动跑满 14 项 check
   属正常（Bundle 矩阵 4 平台 + Python CI 5 + Security 3 + Linting 1 + Desktop 1）。
   等待期可同时处理其他 PR，不要干等一个 job。
10. **上游已独立实现的不再重提（剔除重复）**：提交 PR 前，先 diff 上游 main 是否已
    包含等价实现（2026-08-30 案例：13 个 DEEPCODE 独有 commit 中 5 个已被上游独立实现，
    剔除后只剩 8 个值得提）。复用 `hkuds-deepcode-pr-status-*` 记忆判断哪些是重复。
11. **大改动按模块拆多个 PR 推（C1 方案）**：一次推多 PR 时，每个 PR 分支从
    `upstream/main` 创建（不是从本地旧分支），`git cherry-pick` 指定 commit，独立推送。
    2026-08-30 案例：8 个独有 commit 分 4 个 PR —— testfix(1) / providers(2) / security(2) /
    genai(3)。genai 的 P1 在 4 文件上冲突（runner/session/memory/mcp-runtime），逐个手动解决；
    P2/P3 干净无冲突。**注意**：从旧分支 cherry-pick 会带旧分支的中间状态，尽量从
    upstream/main 新建。
12. **PR 批量创建用浏览器自动化（Playwright）**：每个 PR 走
    `https://github.com/HKUDS/DeepCode/compare/main...<fork>:<branch>?expand=1`，
    填标题 + 描述（`#pull_request_body` textarea），点 "Create pull request"。
    2026-08-30 案例：4 个 PR 全部自动创建成功（#197 testfix / #198 providers /
    #199 security / #200 genai），CI 自动触发。
13. **一个缩进错误可以级联杀死全部 CI**：`runner.py` 中 `def _overflow_reduce(`
    被错误取消缩进为 module-level（0 空格）→ `core.agent_runtime.runner` 导入失败 →
    Python CI 47 个测试全部 collection error + Desktop sidecar `--verify-runtime` 报错 +
    4 个 Bundle job 全挂。**修复顺序**：先修根因（缩进），再修暴露出的二级失败
    （如 `test_every_injected_instruction_source_is_framed` 断言更新）。
    2026-08-30 案例：2 个 commit 修复 3 个 workflow / 14 个 job 全绿。
14. **CI 汇总时 `skipped` ≠ 失败**：用 API 汇总 check-runs 时，`conclusion == "skipped"`
    不是失败（如纯 Python 改动会跳过 Bundle/桌面构建），只有 `failure`/`timed_out` 才算红。
    2026-09-01 案例：脚本把 #197 的 Bundle skipped 误判为红（显示 "0 fail"），
    实际 10 个 run = 9 success + 1 skipped = 全绿。
15. **PR 状态检查最快路径是 API 而非浏览器**：`GET /repos/HKUDS/DeepCode/pulls?state=open`
    拿列表 → 每条 `GET /repos/HKUDS/DeepCode/commits/<head.sha>/check-runs` 拿 CI 汇总，
    `curl` + `python3` 解析一行出报告，比 Playwright 开页面快且可靠（无需登录）。
    2026-09-01 案例：4 个仓库（HKUDS 上游 / fork / DSH 上游 / DSH fork）30 秒内全部查完。
16. **CI 失败详情用 Playwright 抓 job 日志**：check-runs API 的 annotations 只返回
    `"Process completed with exit code 1"`（无具体 test 名）。正确做法是 Playwright 打开
    job 页面 → 点击 "Run tests" 步骤展开 → 从 accessibility snapshot 提取失败 test 名。
    2026-09-01 案例：`pr195` 4 个 CI job 全挂，annotations 只有 exit code 1，
    Playwright 快照才看到 `test_execute_bash_blocklist_survives_case_and_spacing[3]` 全部失败。
17. **他人 PR 的 CI 红 → 诊断 + 留评论，不直接 push**：fork PR（如 rifkir23 的 #195）
    `maintainer_can_modify=True` 只对 HKUDS 维护者有效，fork 贡献者无法 push。
    诊断根因后应在 PR 上发评论说明修复方案，由原作者或维护者处理。
    2026-09-01 案例：#195 的 4 个 CI job 全部失败，诊断出 2 处根因（大小写敏感 + error message 不匹配），
    本地验证修复后发评论 https://github.com/HKUDS/DeepCode/pull/195#issuecomment-5490772001。
18. **新模块零调用者 = Speculative Generality，不提**：纯新增模块如果仓库内**没有 tracked 调用方**，
    提上去会被当成\"为未来设计\"而打回或搁置。提交前用 `rg` 全仓库（排除 `.deepcode/.dsh` 本地定制）
    确认至少一个 tracked 消费者，或先把调用方一起放进同一个小 PR。
    2026-09-10 案例：#227 前本有两个候选（monitor gate / outbox delivery-claim），
    outbox 在 tracked 代码里零调用者 → 弃提，只提 monitor gate（2 文件 +606 行）。
19. **API 重构必须地毯式同步全部调用方（含本地定制目录）**：改名/改签名后，
    除了 tracked 代码，还要 `rg` 搜 `.deepcode/` `.dsh/` 等本地定制里的调用，一并更新并冒烟，
    否则 PR 发出去本地就断（\"落地在前\"铁律的本地侧也必须成立）。
    2026-09-10 案例：`monitor_gate_full` 改名 `monitor_gate` + `store` 改为必传，
    同步更新 `.dsh/cerebellum-scheduler/scheduler.py`，本地 CLI 三态冒烟通过才推 PR。

## 流程（每步有完成条件）

1. **预检** — 读小脑 `hkuds-deepcode-pr-status-*` + GitHub open PR 列表。
   完成条件：说得出当前哪些 PR 开放、各在等谁、有无 #191 式仓库级阻塞。
2. **拆分** — 按模块切成独立可合单元；新文件型优先，行为修改型靠后；
   两两合并顺序无关（或有显式 no-op 兜底）。
   完成条件：每个 PR 有一句独立成立的理由，依赖关系在描述里写明。
3. **本地验证** — `F:\DS-HARNESS\hkuds-ci\DeepCode`：fetch upstream →
   rebase main → `pip install -e ".[test]"` → `pytest -q` → ruff check+format。
   **⚠️ rebase 冲突解决后必须逐文件 `python -c "import <module>"` 验证语法**——
   冲突解决工具可能丢失缩进，导致 IndentationError 到 CI 才暴露
   （2026-08-25 案例：3 个文件缩进丢失）。修复后跑 `ruff check --fix` 自动修 import 排序。
   完成条件：所有改动模块 import 零报错，3.12 / 3.13 全绿，ruff 零告警。
4. **推送建 PR** — 经 `F:\DS-HARNESS\hkuds-ci\push_branch.py`（PAT 自动取自
   F:\DEEPCODE\.env）或 GitHub MCP；PR 描述用 reference.md 模板。
   **⚠️ 推送前通过 API 确认 PR 的 `head.ref`**（分支名可能与本地不同！）。
   2026-08-25 案例：本地 `feat/keyring` 但 PR 跟踪 `pr181/keyring`，推到错误分支名
   导致 PR 不同步、CI 不触发。推送后验证 PR head SHA 已更新。
   完成条件：PR head SHA 已更新，描述含 Summary / Changes / Tests(数量) / Dependency note。
5. **CI 守望** — 四条工作流全完成后按 reference.md《红 CI 诊断》归因：
   仓库级 → 专门小 PR；本 PR → 修复推送；flaky → 加固白名单/重跑并留言说明。
   **CI 日志获取**：用 PAT 调用 `Invoke-RestMethod -Uri "https://api.github.com/repos/HKUDS/DeepCode/actions/jobs/<jobId>/logs"` 下载。
   完成条件：每个红 CI 有归因 + 行动 +（如非本 PR 问题）PR 留言。
6. **评审跟进** — 逐条回应维护者意见；请评审时一句话说清"为何可独立合入"。
   完成条件：每条评论有回复或修复提交，无悬空问题。
7. **积压处理** — 超 3 周无进展：附说明关闭、保留分支、日后拆小重提。
   完成条件：队列中无超 3 周僵尸 PR。
8. **沉淀（强制，不可跳过）** — 每次 PR 行动（建/推/修/关/CI 结果/评审回复）结束后，
   **同一天内**必须：
   a. 更新小脑记忆 `hkuds-deepcode-pr-status-<YYYYMMDD>`（开放清单、各方待办、新教训）
      —— 若当天已有，直接覆盖为最新状态；
   b. 把本次新教训沉淀回本 skill：新增/修正铁律、更新 reference.md 历史案例表；
   c. bump SKILL.md `version`（+0.1）并更新 `date`。
   完成条件：记忆库存在当日 `hkuds-deepcode-pr-status-*` 记录，且
   SKILL.md 版本号 > 上次行动时。两者缺一即视为未完成。
   （⚠️ 2026-09-01 教训：此规则 8/25 后曾断更一周，PR 状态记忆缺失 8/26-8/31，
   用户质问"为什么没实行"。故本条设为不可跳过的收尾步骤。）

## 参考

CI 四线地图、红 CI 诊断树、flaky 目录、维护者画像、PR/留言模板、
环境事实 → 见同目录 [`reference.md`](reference.md)。

## 记忆同步

每次 PR 行动后，更新小脑 `hkuds-deepcode-pr-status-<日期>`（开放清单、
各方待办、新教训）。新教训同时沉淀回本 skill 的铁律/目录。

### 当前状态快照（2026-09-10）

| # | 标题 | CI | 状态 |
|---|------|----|------|
| #227 | feat(schedule): add hash-suppressed monitor gate | ⏳ 12 done / 3 跑中 | 新提交，2026-09-10 |
| #226 | feat(ollama): shared stdlib-only Ollama HTTP client | ✅ 全绿 15/15 | 2026-09-09 |
| #225 | feat(providers): non-function-call model compat fallback | ✅ 全绿 15/15 | 2026-09-09 |
| #224 | feat(providers): opt-in tiered loop routing to cheap models | ✅ 全绿 15/15 | 2026-09-09 |
| #223 | feat(loop): prompt cue guidance for structured/stepwise | ✅ 全绿 15/15 | 2026-09-09 |
| #222 | feat(loop): explicit mitigation for retrieval failure | ✅ 全绿 15/15 | 2026-09-09 |
| #221 | feat(observability): LLMOps five-dimension metric aggregation | ✅ 全绿 15/15 | 2026-09-09 |
| #220 | feat(mcp): supply-chain audit declarations for MCP servers | ✅ 全绿 15/15 | 2026-09-09 |
| #219 | feat(loop): sequential chain builder | ✅ 全绿 15/15 | 2026-09-09 |
| #218 | feat(loop): SLM/LLM subtask complexity routing | ✅ 全绿 15/15 | 2026-09-09 |
| #217 | feat(loop): groundedness spot-check for output validation | ✅ 全绿 15/15 | 2026-09-09 |
| #216 | feat(loop): prompt-injection regression suite (pure mechanical) | ✅ 全绿 15/15 | 2026-09-09 |
| #215 | feat(tools): semantic tool-name suggestion for registry | ✅ 全绿 15/15 | 2026-09-09 |
| #210 | fix(cli,hooks): UTF-8 robustness on Windows (GBK) local | ✅ 全绿 29/29 | 2026-09-08 |

**14 个 open PR 中 13 个已全绿，1 个(#227) CI 跑中。** 全部是自己的 PR（无他人 PR 需跟进）。
deepseek-ai/deepseek-harness 无 open PR。