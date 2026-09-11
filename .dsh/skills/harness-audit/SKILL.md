---
name: harness-audit
description: >
  Agent Work Loop 五维度评估（task-understanding / controlled-execution / change-validation /
  reliable-delivery / learning-capture）。用于对 DEEPCODE 项目（或任意目标仓库）做 AI 编码工作流体检：
  收集证据 → 五维度评分 → 生成证据约束的 findings。源自 QoderAI/better-harness 的 Agent Work Loop 模型，
  已适配 DEEPCODE 组件（telemetry / event_bus / checkpoint / cerebellum）。
  触发关键词：harness audit、工作流体检、五维度评估、agent 工作流评估、work loop 评估、评估我们的 AI 工作流。
version: 2.0.0
author: DeepCode (adapted from QoderAI/better-harness)
source: https://github.com/QoderAI/better-harness
date: 2026-08-27
tags: [harness, workflow, evaluation, meta, agent-work-loop]
extends: deepcode-coach
---

# Harness Audit — Agent Work Loop 五维度评估（v2.0）

> **核心主张**：评估一个 AI 编码工作流，不是看最终 diff 的质量，而是看 **Agent 工作的外循环（Outer Loop）**——
> 项目是否为 Agent 提供了「理解目标 → 受控执行 → 验证变更 → 可靠交付 → 沉淀学习」的完整闭环。
> **缺失的证据保持显式（Missing evidence stays explicit）**：配置的能力 ≠ 已观察到的使用。

## 1. Feedforward + Feedback 闭环

Better Harness 的核心洞察：Agent 工作流的质量取决于**任务开始前的引导**和**任务执行后的反馈**是否形成闭环。

```
┌──────────────────────────────────────────────────────────────┐
│                     Agent Work Loop                          │
│                                                              │
│  前馈（Feedforward）             反馈（Feedback）              │
│  ┌──────────────────┐          ┌──────────────────┐          │
│  │ AGENTS.md / spec  │          │ Linter / Tests    │          │
│  │ Skills / Rules    │ ──────→  │ Hooks / 观测      │          │
│  │ 验收标准 / AC     │  执行    │ CI/CD 结果        │          │
│  └──────────────────┘          └──────────────────┘          │
│         ↑                             ↑                       │
│         │                             │                       │
│  ┌──────┴─────────────────────────────┴──────┐               │
│  │           Task Episode                    │               │
│  │  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐ │               │
│  │  │ 理解  │→│ 执行  │→│ 验证  │→│ 交付  │ │               │
│  │  └──────┘  └──────┘  └──────┘  └──────┘ │               │
│  └───────────────────────────────────────────┘               │
│                                                              │
│  经验沉淀（Learning Capture）                                 │
│  ┌──────────────────────────────────────────┐                │
│  │ 检测 → 干预 → 验证 → 沉淀 → 纵向追踪     │                │
│  └──────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────┘
```

## 2. 五维度模型（15 检查项，稳定标识）

| 维度 ID | 维度标签 | 3 个检查项 |
|---|---|---|
| `task-understanding` | 任务理解 | `goal-understanding` 意图与验收 · `relevant-context` 相关上下文 · `scope-boundary` 范围边界 |
| `controlled-execution` | 受控执行 | `instruction-led-start` 可复现启动 · `supported-operation` 受支持操作 · `permission-boundary` 权限边界 |
| `change-validation` | 变更验证 | `relevant-check` 相关验证 · `failure-repair` 失败诊断与修复 · `validate-again` 修复后再验证 |
| `reliable-delivery` | 可靠交付 | `acceptance-evidence` 交付验收 · `high-risk-approval` 高风险审批 · `rollback-recovery` 回滚/恢复 |
| `learning-capture` | 学习沉淀 | `lifecycle-repeat-detection` 重复检测 · `loop-engineering` 循环工程 · `later-validation` 纵向验证 |

**Review Unit（审查单元）**：一个 **Task Episode** = 一个用户目标 + 一个验收边界。行为声明必须绑定到同一目标/动作/结果，禁止跨 Episode 拼接。

## 3. 证据状态机（7 级，v2.0 新增）

v2.0 引入完整的 7 级证据状态，与 Better Harness 官方对齐：

```
Outcome-supported  → 有后续可比较结果证明效果（评分上限 100）
    Exercised      → 在任务中被实际执行过（评分上限 94）
      Wired        → 机制已接通，可到达（评分上限 84）
        Present   → 资产存在（评分上限 74）
         Missing  → 确认缺失（评分上限 59）
      Unobserved   → 无法观测（评分上限 59）
  Not applicable   → 不适用（评分上限 59）
```

**规则**：
- 证据状态是**分数上限**，不是分数公式。分数永远不高于最高证据状态对应的上限。
- 分数超过 75 需要额外的「已检查的源代码或测试所有权 + 已执行的验证路径」。
- 分数永远不创建/抑制 finding。Finding 需要「已检查的差距 + 有界影响 + 所有者对齐的修复 + 验证路径」。
- 单一 Agent 整数评分 35–100；`null` 属于未解决槽位（不得给分）。
- Learning Capture 维度使用 35–100 整数评分，35 仅表示"完成了有边界的审查"。

## 4. Finding 结构（v2.0 增强）

每个 Finding 包含完整修复计划：

```json
{
  "id": "finding-001",
  "title": "缺少高风险审批路径",
  "severity": "High",
  "reason": "安全/权限类 PR 不需要额外审批人",
  "dimensionRefs": ["reliable-delivery"],
  "evidenceStates": {"high-risk-approval": "Missing"},
  "feedforwardGuide": "在 CONTRIBUTING.md 中注明安全变更需 @Zongwei9888 审批",
  "feedbackSensor": "在 CI 中增加安全变更自动标记 + 审批人分配",
  "aiFixPrompt": "给 Agent 的可执行修复指令",
  "expectedArtifact": "CONTRIBUTING.md 更新 + CI 配置",
  "expectedOutput": [
    "安全变更 PR 自动标记 `security` label",
    "审批人自动分配为 @Zongwei9888"
  ],
  "acceptanceCheck": [
    "新建安全 PR 时 label 自动添加",
    "非安全 PR 不受影响"
  ],
  "repairProgress": "pending"
}
```

### Finding-bound Repair Progress（v2.0 新增）

修复后使用独立评审者判断：`verified` / `partial` / `blocked`。维度分数不变，仅更新 `repairProgress`。只有后续的 Task Episode 才能证明闭环确实改进。

## 5. 执行流程（7 步，v2.0 增强）

### Step 1 — 解析范围
确定目标仓库（默认 `F:\DEEPCODE`）、时间窗（`--since`/`--until`，默认近 30 天）、深度（`quick`=3 资产 / `normal`=5 资产 / `full`=全部）。

### Step 2 — 收集前馈证据
扫描项目前馈机制：
- `AGENTS.md` / `CLAUDE.md` / `CONTRIBUTING.md` — 是否存在？内容是否明确？
- Skills 目录 — 是否覆盖了项目所需能力？
- 验收标准 — 是否有明确的 "done" 定义？
- 规则文件 — 权限、工具限制、MCP 配置

### Step 3 — 收集反馈证据
扫描项目反馈机制：
- CI 配置 — 是否有多条流水线？最近 30 天通过率？
- Hooks 注册 — PreToolUse/PostToolUse/SessionStart/SessionEnd
- 测试覆盖率 — 测试文件数量、最近运行结果
- 小脑记忆 — cerebellum 经验条目、会话摘要

### Step 4 — 三通道独立评估
| 通道 | 关注域 | 输出 |
|---|---|---|
| Session Evidence | 遥测、tool call 日志 → `instruction-led-start` / `validate-again` | 每检查项证据状态 |
| Project Harness | AGENTS.md、Skills、Rules、permissions、hooks、checkpoint、tests | 每检查项证据状态 |
| Learning Capture | cerebellum 记忆/经验/知识库、telemetry 学习信号 | 每检查项证据状态 |

### Step 5 — 调和评分
- 保留每个不同且受支持的 finding
- 分数受证据状态上限约束
- 生成 findings.json

### Step 6 — 生成报告
三件套：`findings.json` + `report.md` + `report.html`

### Step 7 — 沉淀到小脑
将审计结果记录到 cerebellum：
- 记录经验：`cerebellum_experience_record(task="harness audit for <project>")`
- 检测学习循环：`cerebellum_learning_loop_detect()`
- 更新经验图谱：`cerebellum_experience_graph(rebuild=True)`

## 6. DEEPCODE 组件映射表（增强版）

| 维度 | 检查项 | DEEPCODE 证据源 | 期望状态 |
|---|---|---|---|
| task-understanding | goal-understanding | AGENTS.md, CLAUDE.md, session header | Wired+ |
| task-understanding | relevant-context | system-prompt, skills INDEX.md | Wired+ |
| task-understanding | scope-boundary | permission config, tool registry | Wired+ |
| controlled-execution | instruction-led-start | preset cordis.yml, boot plugin | Exercised+ |
| controlled-execution | supported-operation | MCP 工具列表, skill provider | Wired+ |
| controlled-execution | permission-boundary | sandbox config, permission profile | Exercised+ |
| change-validation | relevant-check | pytest 配置, CI workflows | Exercised+ |
| change-validation | failure-repair | CI 日志, telemetry error records | Present+ |
| change-validation | validate-again | CI 重跑记录, hooks onError | Present+ |
| reliable-delivery | acceptance-evidence | PR review comments, approval | Present+ |
| reliable-delivery | high-risk-approval | 安全/权限 PR 审批流程 | Present+ |
| reliable-delivery | rollback-recovery | git revert, checkpoint rollback | Present+ |
| learning-capture | lifecycle-repeat-detection | cerebellum learning_loop_detect | Exercised+ |
| learning-capture | loop-engineering | cerebellum experience, skill 更新 | Exercised+ |
| learning-capture | later-validation | 跨会话经验图谱, 重复问题追踪 | Present+ |

## 7. 输出规范

- **报告输出位置**：`F:\DEEPCODE\analysis_output\harness_audit\YYYY-MM-DD\`
- **文件名**：`findings.json` + `report.md` + `report.html`
- **语言**：跟随用户对话语言
- **编码**：UTF-8

## 8. 与 Better Harness 的差异（落地裁剪声明）

1. **语言**：模型文件为中文，完整英文版见上游仓库。
2. **引擎**：DSH 用 Python + PowerShell 脚本，通过 MCP 工具补充动态证据。
3. **证据收集**：静态文件扫描 + 动态 MCP 查询 + 小脑记忆检索 三通道。
4. **分数**：采用同一证据状态机与上限表，保证跨仓库可比。
5. **合规**：引擎只读；不执行任何破坏性操作。
6. **报告**：HTML 报告使用 DSH 主题，而非 Better Harness 的 Studio 主题。