# Agent Work Loop 评估模型（中文精简版）

> 源自 QoderAI/better-harness `models/agent-work-loop.md`（46KB 完整英文版），
> 本文件为 DEEPCODE 落地裁剪版，保留稳定审查标识与全部评分规则。

## 1. 核心概念

**Agent Work Loop（Agent 工作外循环）**：Agent 在一个任务中的完整工作回路：

```
理解目标 → 受控执行 → 验证变更 → 可靠交付 → 沉淀学习
   ↑                                              │
   └────────────── 下一个任务复用 ←───────────────┘
```

评估对象不是最终 diff，而是这个**回路本身**是否被项目"架设"（harness）好。

**Task Episode（任务片段）** = 一个用户目标 + 一个验收边界。
- 所有行为声明必须绑定到**同一** 目标/动作/结果；
- 禁止跨 Episode 拼接证据（e.g. "目标 A 的完成"用"任务 B 的测试"来证明）。

## 2. 五维度 × 15 检查项

| 维度 | 检查项 | 回答的问题（Reader Question） |
|---|---|---|
| **任务理解** | `goal-understanding` | Agent 是否知道"什么算做完"？意图与验收标准是否明确？ |
| | `relevant-context` | Agent 能否找到并加载与该任务相关的上下文（而非全部）？ |
| | `scope-boundary` | Agent 是否知道"不要碰什么"？范围边界是否被约束？ |
| **受控执行** | `instruction-led-start` | 同样的任务是否总能以同样的方式开始（可复现启动）？ |
| | `supported-operation` | Agent 操作是否走受支持/受管制的路径（而非 ad-hoc）？ |
| | `permission-boundary` | 敏感操作是否有权限边界？边界是否机械执行？ |
| **变更验证** | `relevant-check` | 验证是否与变更相关（而非"有验证就行"）？ |
| | `failure-repair` | 失败后 Agent 能否诊断根因并修复（而非绕过）？ |
| | `validate-again` | 修复后是否**重新验证**（而非声称完成）？ |
| **可靠交付** | `acceptance-evidence` | 交付是否有验收证据（而非"我认为完成了"）？ |
| | `high-risk-approval` | 高风险变更是否经过审批/门禁？ |
| | `rollback-recovery` | 交付失败后是否有回滚/恢复路径？ |
| **学习沉淀** | `lifecycle-repeat-detection` | 重复工作是否被检测到（2 个可比较 Episode 即可触发）？ |
| | `loop-engineering` | 重复工作是否被固化到资产（Skill/Spec/命令）而非重做？ |
| | `later-validation` | 固化的资产后来是否被验证过（纵向验证）？ |

## 3. 证据状态机

证据从「配置」到「被证明有效」分五级。**关键原则：配置的能力 ≠ 已观察到的使用。**

| 状态 | 判定标准 | 例（以 hooks 为例） |
|---|---|---|
| `Missing` | 资产不存在 | 没有 hooks 配置 |
| `Present` | 资产存在 | hooks 文件存在 |
| `Wired` | 已接线到事件 | hook 注册到 afterWrite 事件 |
| `Exercised` | 实际演练过 | 日志/DB 记录该 hook 被触发过 |
| `Outcome-supported` | 有结果证明有效 | 有评测记录证明该 hook 拦截了错误 |
| `Unobserved` | 可能配置了但本次审查未观察到 | 未在时间窗内观察到触发 |
| `Not applicable` | 对该项目类型不适用 | 纯文档项目无部署回滚 |

## 4. 分数规则（上限约束，非公式）

| 最高证据状态 | 绝对分数上限 |
|---|---|
| `Missing` / `Unobserved` / `Not applicable` | 59 |
| `Present` | 74 |
| `Wired` | 84 |
| `Exercised` | 94 |
| `Outcome-supported` | 100 |

- 维度分数 = 该维度 3 个检查项中**最高证据状态**对应的上限，再按检查项达成度微调（≤ 上限）。
- 单一 Agent 整数评分 35–100；`null` = 未解决槽位，不得给分。
- 35 地板表示"完成了有界的审查"（比没审查强），不表示工作流优秀。
- **Learning Capture 特殊规则**：分数不来自发现数量；只反映"检测→固化→验证"闭环的完整性。

## 5. 发现（Finding）生成规则

分数永远**不创建/抑制** finding。一条合格 finding 必须同时满足：

1. **已检查的差距**：审查者实际检查过该项，且存在差距；
2. **有界影响**：说明影响的边界（哪类任务、多大范围）；
3. **所有者对齐的修复**：修复动作有明确 owner（人/组件/Agent）；
4. **验证路径**：修复后如何验证（验收标准）。

**单独不足以构成 finding**：文件计数、文件名、资产存在性、严重性、年龄、变更量、分数、发现数量。

**典型 finding 结构**：
```
id / title / severity / reason(证据链) / dimensionRefs / evidenceStates
aiFixPrompt(给 Agent 的修复指令) / expectedArtifact / expectedOutput(验证路径)
```

## 6. 支持跟踪（Support Track）

| 阶段 | 区间 | 关注点 |
|---|---|---|
| Bootstrap | 0→1 | 先跑通最小闭环（一个真实任务走完五维度） |
| Operationalize | 1→60 | 让每个维度至少达到 Wired（接线） |
| Optimize | 60→100 | 每个维度至少一个检查项达到 Exercised/Outcome-supported |
| Undetermined | — | 证据不足无法判定 |

## 7. 五维度设计原理（来源）

| 维度 | 来源 |
|---|---|
| Task Understanding | OpenAI Harness Engineering（specified intent、repository-local legibility、enforceable architecture） |
| Controlled Execution | Harness Engineering（isolated startup、agent-accessible tools、local observability、mechanically enforced boundaries） |
| Change Validation | Google Testing Overview + OpenTelemetry Logs spec |
| Reliable Delivery | GitHub protected branch contract |
| Learning Capture | Google SRE Postmortem Culture |

> 来源只解释模型**形状**，不冻结术语/运行时版本/数值分数。
