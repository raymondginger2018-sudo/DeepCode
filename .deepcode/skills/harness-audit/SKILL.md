---
name: harness-audit
description: >
  Agent Work Loop 五维度评估（task-understanding / controlled-execution / change-validation /
  reliable-delivery / learning-capture）。用于对 DEEPCODE 项目（或任意目标仓库）做 AI 编码工作流体检：
  收集证据 → 五维度评分 → 生成证据约束的 findings。源自 QoderAI/better-harness 的 Agent Work Loop 模型，
  已适配 DEEPCODE 组件（telemetry / event_bus / checkpoint / cerebellum）。
  触发关键词：harness audit、工作流体检、五维度评估、agent 工作流评估、work loop 评估、评估我们的 AI 工作流。
version: 1.0.0
author: DeepCode (adapted from QoderAI/better-harness)
source: https://github.com/QoderAI/better-harness
date: 2026-08-04
tags: [harness, workflow, evaluation, meta]
---

# Harness Audit — Agent Work Loop 五维度评估

> **核心主张**：评估一个 AI 编码工作流，不是看最终 diff 的质量，而是看 **Agent 工作的外循环（Outer Loop）**——
> 项目是否为 Agent 提供了「理解目标 → 受控执行 → 验证变更 → 可靠交付 → 沉淀学习」的完整闭环。
> **缺失的证据保持显式（Missing evidence stays explicit）**：配置的能力 ≠ 已观察到的使用。

## 1. 五维度模型（15 检查项，稳定标识）

| 维度 ID | 维度标签 | 3 个检查项 |
|---|---|---|
| `task-understanding` | 任务理解 | `goal-understanding` 意图与验收 · `relevant-context` 相关上下文 · `scope-boundary` 范围边界 |
| `controlled-execution` | 受控执行 | `instruction-led-start` 可复现启动 · `supported-operation` 受支持操作 · `permission-boundary` 权限边界 |
| `change-validation` | 变更验证 | `relevant-check` 相关验证 · `failure-repair` 失败诊断与修复 · `validate-again` 修复后再验证 |
| `reliable-delivery` | 可靠交付 | `acceptance-evidence` 交付验收 · `high-risk-approval` 高风险审批 · `rollback-recovery` 回滚/恢复 |
| `learning-capture` | 学习沉淀 | `lifecycle-repeat-detection` 重复检测 · `loop-engineering` 循环工程 · `later-validation` 纵向验证 |

**Review Unit（审查单元）**：一个 **Task Episode** = 一个用户目标 + 一个验收边界。行为声明必须绑定到同一目标/动作/结果，禁止跨 Episode 拼接。

## 2. 证据状态机 + 分数上限

证据从「配置」到「被证明有效」分五级。**分数不是公式计算，而是上限约束**：

| 最高证据状态 | 含义 | 绝对分数上限 |
|---|---|---|
| `Missing` / `Unobserved` / `Not applicable` | 不存在 / 未观察到 / 不适用 | 59 |
| `Present` | 资产存在（文件/配置项） | 74 |
| `Wired` | 已接线（被注册到事件/被配置加载） | 84 |
| `Exercised` | 已演练（有日志/遥测/DB 记录证明实际发生） | 94 |
| `Outcome-supported` | 结果支持（有 benchmark/评测/成功案例） | 100 |

**规则**：
- 单一 Agent 整数评分 35–100；`null` 属于未解决槽位（不得给分）。
- 分数永远不创建/抑制 finding；finding 需要「已检查的差距 + 有界影响 + 所有者对齐的修复 + 验证路径」。
- 文件存在、文件名、严重性、年龄、变更量、计数、分数——单独出现都不足以成为 finding。

## 3. 执行流程（5 步）

### Step 1 — 解析范围
确定目标仓库（默认 `F:\DEEPCODE`）、时间窗（`--since`/`--until`，默认近 30 天）、深度（`quick`=3 资产+前 7 天 / `normal`=5 资产+前 30 天）。

### Step 2 — 收集证据（证据包）
运行引擎收集三类证据，产出版本化证据包：

```bash
python -m scripts.harness_audit.main --target F:\DEEPCODE --since 30d --format json
```

引擎只读，不修改任何文件；遵循 DB-First 规范（只读 SQLite，不调外部 API）。

### Step 3 — 三通道独立评估（与 better-harness 对齐，最多 3 个委托 agents）
| 通道 | 关注域 | 输出 |
|---|---|---|
| Session Evidence | 会话记录、遥测、tool call 日志 → `instruction-led-start` / `validate-again` | 每检查项证据状态 |
| Project Harness Evidence | AGENTS.md、Skills、Rules、permissions、hooks、checkpoint、tests → 其余 4 维度 | 每检查项证据状态 |
| Agent Customize Evidence | cerebellum 记忆/经验/知识库、telemetry 学习信号 → `learning-capture` 3 项 | 每检查项证据状态 |

每个通道有严格简报隔离，互不共享证据；任何未被观察到的行为保持 `Unobserved`，不推断、不打分。

### Step 4 — Lead 调和与评分
读取 `findings.input.json` + `models/agent-work-loop.md`：
- 保留每个**不同且受支持**的 finding（去重，合并同源）；
- 冻结严重性与维度分数后才开始确定优先级动作；
- 分数受 Step 2 证据状态上限约束。

### Step 5 — 渲染报告
输出三件套：`findings.json`（机器可读契约）+ 中文 Markdown 报告 + 可选 HTML。报告结构遵循 docs-generator 规范（Evidence → Finding → Path 链）。

## 4. 报告契约（findings.json）

```json
{
  "summary": {
    "projectName": "deepcode",
    "locale": "zh-CN",
    "modelId": "agent-work-loop-v1",
    "reportContractVersion": 1,
    "overview": "…",
    "dimensions": [
      {"id": "task-understanding", "label": "任务理解", "score": 72, "summary": "…"},
      {"id": "controlled-execution", "label": "受控执行", "score": 68, "summary": "…"},
      {"id": "change-validation", "label": "变更验证", "score": 76, "summary": "…"},
      {"id": "reliable-delivery", "label": "可靠交付", "score": 48, "summary": "…"},
      {"id": "learning-capture", "label": "学习沉淀", "score": 35, "summary": "…"}
    ],
    "aiAgentPractice": {
      "inspectedSurfaces": ["Rules", "Skills", "Permissions", "Hooks", "Checkpoint", "Tests", "Telemetry", "Memory"],
      "coverageRows": []
    }
  },
  "findings": [
    {
      "id": "finding-id",
      "title": "…",
      "severity": "High|Medium|Low",
      "reason": "证据链（已检查的差距 + 有界影响）",
      "dimensionRefs": ["reliable-delivery"],
      "evidenceStates": {"rollback-recovery": "Present"},
      "aiFixPrompt": "给 Agent 的可执行修复指令",
      "expectedArtifact": "Runbook|Config|Script|Doc",
      "expectedOutput": ["验证路径 1", "验证路径 2"]
    }
  ]
}
```

## 5. DEEPCODE 组件映射表（证据源）

| 维度 | DEEPCODE 证据源（Evidence Source） |
|---|---|
| task-understanding | `AGENTS.md`、`CLAUDE.md`、`deepcode_config.json`、`.deepcode/skills/INDEX.md` |
| controlled-execution | `permission_config`（deepcode-engine）、`tool_registry`、`hooks` 注册表 |
| change-validation | `deepcode-hooks`（beforeWrite/afterWrite/postTask/onError）、`checkpoint_save/diff`、`tests/` |
| reliable-delivery | `checkpoint_rollback`、`git_integration`、`tests/` 结果、telemetry 交付记录 |
| learning-capture | `deepcode-cerebellum`（data/cerebellum.db 记忆/经验/会话）、`telemetry_data/`、`knowledge-vault/` |

## 6. 输出规范

- **报告输出位置**：`F:\DEEPCODE\analysis_output\harness_audit\YYYY-MM-DD\`
- **文件名**：`findings.json` + `report.md`（+ 可选 `report.html`）
- **语言**：跟随用户对话语言（中文对话出中文报告）
- **编码**：UTF-8

## 7. 与 Better Harness 的差异（落地裁剪声明）

1. **语言**：模型文件为中文精简版（`models/agent-work-loop.md`），完整英文版见上游仓库。
2. **引擎**：better-harness 用 Node.js CLI + 宿主插件；DEEPCODE 用 Python stdlib 引擎（零依赖、可离线运行），并通过 MCP 工具（deepcode-engine / deepcode-hooks / deepcode-cerebellum）补充动态证据。
3. **证据收集**：静态文件扫描（AGENTS.md/skills/tests/telemetry/cerebellum.db）+ 动态 MCP 查询（可选）双通道。
4. **分数**：采用同一证据状态机与上限表，保证跨仓库可比。
5. **合规**：引擎只读；不执行任何破坏性回滚来改善证据（recovery-evidence 规范）。
