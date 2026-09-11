# Harness Audit 参考资料

## 1. 环境事实

| 项 | 值 |
|---|---|
| 审计目标 1 | `F:\DEEPCODE`（DeepCode 项目） |
| 审计目标 2 | `F:\DEEPCODE\deepseek-harness`（DSH） |
| 审计目标 3 | `F:\DS-HARNESS\hkuds-ci\DeepCode`（HKUDS/DeepCode PR） |
| 报告输出 | `F:\DEEPCODE\analysis_output\harness_audit\YYYY-MM-DD\` |
| 小脑数据库 | `F:\DEEPCODE\.dsh\skills\deepcode-cerebellum\data\cerebellum.db` |
| 遥测数据 | `F:\DEEPCODE\telemetry_data\` |

## 2. 审计脚本

### coach_audit.ps1 — 一键审计

```powershell
# 用法: powershell -ExecutionPolicy Bypass -File F:\DS-HARNESS\hkuds-ci\coach_audit.ps1 [-Target F:\DEEPCODE] [-Depth normal]
param(
    [string]$Target = "F:\DEEPCODE",
    [ValidateSet("quick", "normal", "full")]
    [string]$Depth = "normal"
)
```

### 手动审计步骤

```powershell
# 1. 收集项目资产清单
$env:GH_TOKEN = (Get-Content "$env:USERPROFILE\.config\gh\hosts.yml" | Select-String "oauth_token" | ForEach-Object { ($_ -replace ".*oauth_token: ", "").Trim() })

# 2. 检查 AGENTS.md / CLAUDE.md
$agentsDoc = Get-Content "$Target\AGENTS.md" -Raw -ErrorAction SilentlyContinue
$agentsDoc.Length  # 是否 > 0

# 3. 检查 Skills 目录
$skills = Get-ChildItem "$Target\.dsh\skills" -Directory -ErrorAction SilentlyContinue
$skills.Count

# 4. 检查 CI 状态
gh api repos/HKUDS/DeepCode/actions/runs --per-page 5 --jq '.workflow_runs[] | {name, status, conclusion, created_at}'

# 5. 检查小脑状态
# 通过 cerebellum MCP 工具
```

## 3. 检查项详细清单

### task-understanding / goal-understanding
**问题**：项目是否有明确的"目标 → 完成"定义？
**证据**：
- AGENTS.md 是否存在并描述项目目标
- session header 是否包含目标字段
- PR 描述是否有明确的 "done" 条件
**期望**：Wired+（AGENTS.md 存在 + session 传递目标）

### task-understanding / relevant-context
**问题**：Agent 是否能获取到完成任务所需的所有上下文？
**证据**：
- system-prompt 是否包含项目上下文
- skills INDEX.md 是否列出可用能力
- 是否有 knowledge-vault 或文档库
**期望**：Wired+

### task-understanding / scope-boundary
**问题**：Agent 是否知道什么可以做、什么不可以做？
**证据**：
- permission config 是否定义了操作边界
- tool registry 是否限制了可用工具
- MCP 配置是否控制了外部访问
**期望**：Wired+

### controlled-execution / instruction-led-start
**问题**：项目能否通过配置自动启动 Agent 环境？
**证据**：
- preset cordis.yml 是否存在
- boot plugin 是否自动加载
- 是否有标准化的启动流程文档
**期望**：Exercised+（有日志证明启动过）

### controlled-execution / supported-operation
**问题**：Agent 的工作是否通过受支持的工具路径进行？
**证据**：
- MCP 工具列表是否完整
- skill provider 是否注册了正确的能力
- 是否有自定义命令/脚本
**期望**：Wired+

### controlled-execution / permission-boundary
**问题**：Agent 是否在权限边界内执行？
**证据**：
- sandbox 配置是否启用
- permission profile 是否定义了模式
- 是否有权限审计日志
**期望**：Exercised+（有日志证明权限被检查过）

### change-validation / relevant-check
**问题**：修改是否有对应的验证？
**证据**：
- pytest 配置是否存在
- CI 工作流是否覆盖了主要功能
- 测试文件数量与代码规模的比例
**期望**：Exercised+

### change-validation / failure-repair
**问题**：验证失败时能否诊断和修复？
**证据**：
- CI 日志是否可获取
- telemetry 是否记录错误
- 是否有 error handling 机制
**期望**：Present+

### change-validation / validate-again
**问题**：修复后是否重新验证？
**证据**：
- CI 重跑记录
- hooks onError 是否触发重试
- 是否有"修复→验证"的闭环流程
**期望**：Present+

### reliable-delivery / acceptance-evidence
**问题**：交付是否有验收记录？
**证据**：
- PR review comments
- 审批记录（approval）
- 验收检查清单
**期望**：Present+

### reliable-delivery / high-risk-approval
**问题**：高风险变更是否有额外审批？
**证据**：
- 安全/权限 PR 是否有额外 label
- 是否指定了特定审批人
- 是否有安全审查流程
**期望**：Present+

### reliable-delivery / rollback-recovery
**问题**：出问题时能否回滚？
**证据**：
- git revert 记录
- checkpoint rollback 功能
- 回滚文档/runbook
**期望**：Present+

### learning-capture / lifecycle-repeat-detection
**问题**：系统能否检测重复问题？
**证据**：
- cerebellum learning_loop_detect 输出
- 是否有重复 issue/PR 检测
- 失败模式是否被归类
**期望**：Exercised+

### learning-capture / loop-engineering
**问题**：检测到的问题是否被转化为可复用的改进？
**证据**：
- cerebellum experience 条目
- skill 是否根据经验更新
- 是否有"修复→沉淀→不再犯"的流程
**期望**：Exercised+

### learning-capture / later-validation
**问题**：改进是否经得起时间考验？
**证据**：
- 跨会话经验图谱
- 重复问题追踪记录
- 历史报告对比
**期望**：Present+

## 4. 评分指南

### 分数上限表

| 最高证据状态 | 分数上限 | 含义 |
|---|---|---|
| Missing / Unobserved / N/A | 59 | 机制不存在或无法观测 |
| Present | 74 | 资产存在但未使用 |
| Wired | 84 | 已接线但未演练 |
| Exercised | 94 | 有实际使用记录 |
| Outcome-supported | 100 | 有后续结果证明效果 |

### 分数解读

| 分数范围 | 含义 | 行动 |
|---|---|---|
| 90-100 | 优秀，有证据证明效果 | 保持，可作为最佳实践 |
| 75-89 | 良好，机制已启用 | 寻找可改进的细节 |
| 60-74 | 及格，机制存在但未充分使用 | 推动实际演练 |
| 35-59 | 需改进，机制缺失或未启用 | 优先修复 |

## 5. 报告模板

### HTML 报告（简化版）

```html
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Harness Audit Report</title>
<style>
  body { font-family: system-ui; max-width: 960px; margin: auto; padding: 2em; }
  .dimension { border: 1px solid #ddd; border-radius: 8px; padding: 1em; margin: 1em 0; }
  .score-bar { height: 8px; background: #eee; border-radius: 4px; }
  .score-fill { height: 8px; border-radius: 4px; }
  .finding { border-left: 4px solid; padding: 0.5em 1em; margin: 0.5em 0; }
  .finding.High { border-color: #e53e3e; }
  .finding.Medium { border-color: #dd6b20; }
  .finding.Low { border-color: #d69e2e; }
</style></head>
<body>
  <h1>Harness Audit Report</h1>
  <p>Project: {{projectName}} | Date: {{date}}</p>
  <div id="dimensions">{{dimensions}}</div>
  <div id="findings">{{findings}}</div>
</body></html>
```

## 6. 历史审计记录

| 日期 | 目标 | 维度评分 | 关键 Finding | 修复状态 |
|---|---|---|---|---|
| TBD | TBD | TBD | TBD | TBD |