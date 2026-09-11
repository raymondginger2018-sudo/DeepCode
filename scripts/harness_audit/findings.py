"""发现生成模块：从证据差距生成结构化的 Finding。

发现规则（对齐 QoderAI/better-harness）：
- 分数永远**不创建/抑制** finding
- 一条合格 finding = 已检查的差距 + 有界影响 + 所有者对齐的修复 + 验证路径
- 单独的文件名/计数/存在性不足以保证 finding 质量
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .evidence import ItemEvidence

SEVERITY_RANK = {"Low": 0, "Medium": 1, "High": 2}


@dataclass
class Finding:
    """单条发现。"""
    id: str
    title: str
    severity: str
    reason: str                 # 证据链（为什么是差距）
    dimensionRefs: list         # 关联维度
    itemRefs: list              # 关联检查项
    aiFixPrompt: str            # 给 Agent 的修复指令
    expectedArtifact: str       # 预期产物（SKILL/配置/脚本...）
    expectedOutput: list        # 验证路径（修复后如何证明有效）


# 检查项 ID → 差距描述模板（当证据状态不足时触发）
_GAP_RULES: dict[str, dict] = {
    "goal-understanding": {
        "min_state": "Wired",
        "title": "任务意图与验收标准缺少权威定义",
        "artifact": "AGENTS.md / CLAUDE.md 中的验收标准章节",
        "fix": "在项目根目录建立 AGENTS.md，明确每类任务的『完成定义』（DoD）与验收清单，并让 Agent 每次开工前先引用。",
        "output": ["AGENTS.md 含验收标准章节", "Agent 在任务开始时引用 DoD"],
    },
    "relevant-context": {
        "min_state": "Wired",
        "title": "上下文加载缺乏路由，Agent 易被无关信息干扰",
        "artifact": ".deepcode/skills/INDEX.md 或上下文路由规则",
        "fix": "维护技能/文档路由索引，让 Agent 按任务类型加载相关上下文而非全量。",
        "output": ["INDEX.md 覆盖全部可路由资产", "任务中上下文引用量下降"],
    },
    "scope-boundary": {
        "min_state": "Present",
        "title": "范围边界未声明，存在越界改动风险",
        "artifact": "deepcode_config.json 的 scope/ignore 配置",
        "fix": "在配置中声明禁止触碰的路径/文件（如 data/*、vendor/*），并在权限层机械执行。",
        "output": ["配置含 ignore 规则", "越界写入被权限层拦截"],
    },
    "instruction-led-start": {
        "min_state": "Wired",
        "title": "缺少可复现启动路径，任务开局不可控",
        "artifact": "run.bat / run.sh 启动脚本 + 文档",
        "fix": "提供一键启动脚本，并在 CLAUDE.md/README 中描述启动流程，保证同任务同起点。",
        "output": ["启动脚本存在且可执行", "两次启动行为一致"],
    },
    "supported-operation": {
        "min_state": "Wired",
        "title": "Agent 操作路径缺少受管约束，易走 ad-hoc 通道",
        "artifact": "MCP 服务器注册 / 工具白名单",
        "fix": "收敛外部操作到受支持的 MCP/脚本通道，禁止 Agent 绕过工具注册表直接调 API。",
        "output": ["操作统一走注册通道", "工具调用日志可审计"],
    },
    "permission-boundary": {
        "min_state": "Wired",
        "title": "敏感操作缺少机械权限边界",
        "artifact": "permission_config.json / deepcode-engine 权限配置",
        "fix": "启用权限审批：高风险操作（写关键文件/执行危险命令）必须经审批，且审批不可被 Agent 绕过。",
        "output": ["权限配置生效", "高风险操作触发审批"],
    },
    "relevant-check": {
        "min_state": "Wired",
        "title": "验证与变更的相关性不足（有验证≠验证对了）",
        "artifact": "tests/ + pytest 配置",
        "fix": "建立与变更对应的验证资产（单测/冒烟），要求每次变更运行相关测试而非任意测试。",
        "output": ["测试覆盖变更路径", "CI 中运行相关测试"],
    },
    "failure-repair": {
        "min_state": "Wired",
        "title": "失败后缺少诊断-修复闭环，Agent 倾向绕过",
        "artifact": "onError / PostToolUseFailure hooks",
        "fix": "接线失败事件 hook，失败时记录上下文并强制 Agent 先诊断根因再修复，禁止静默绕过。",
        "output": ["失败事件被捕获", "修复后附根因说明"],
    },
    "validate-again": {
        "min_state": "Wired",
        "title": "修复后未强制复验，存在『声称完成』风险",
        "artifact": "afterWrite/checkpoint_diff 复验机制",
        "fix": "在修复后自动触发复验（重新运行测试/对比 diff），复验通过前不得标记完成。",
        "output": ["修复后自动复验", "复验失败会阻断交付"],
    },
    "acceptance-evidence": {
        "min_state": "Wired",
        "title": "交付缺乏验收证据（自认为完成 ≠ 完成）",
        "artifact": "验收清单 + 测试报告归档",
        "fix": "要求每次交付附验收证据（测试通过记录/截图/输出样例），证据缺失不得宣称完成。",
        "output": ["交付附验收证据", "验收证据可回溯"],
    },
    "high-risk-approval": {
        "min_state": "Present",
        "title": "高风险变更缺少审批门禁",
        "artifact": "permission_approve / 审批配置",
        "fix": "定义高风险变更清单（生产写入/删除/批量执行），接入审批工具，未批准不得执行。",
        "output": ["高风险操作触发审批", "审批记录可审计"],
    },
    "rollback-recovery": {
        "min_state": "Wired",
        "title": "失败交付缺少回滚/恢复路径",
        "artifact": "checkpoint_save/diff/rollback 机制",
        "fix": "变更前建立 checkpoint，失败时可回滚；文档化恢复步骤并定期演练。",
        "output": ["checkpoint 覆盖关键变更", "回滚演练记录"],
    },
    "lifecycle-repeat-detection": {
        "min_state": "Wired",
        "title": "重复工作未被检测，浪费在重做",
        "artifact": "cerebellum 重复模式检测",
        "fix": "基于记忆/经验数据检测重复任务（2 个可比较 Episode 即触发），并标记重复工作来源。",
        "output": ["重复任务被标记", "检测结果进入经验库"],
    },
    "loop-engineering": {
        "min_state": "Wired",
        "title": "重复工作未固化为可复用资产",
        "artifact": "SKILL.md / Loop Spec",
        "fix": "将重复工作固化为 Skill/流程资产，让下一次任务直接复用而非从头做。",
        "output": ["重复工作有对应 Skill", "新任务复用资产执行"],
    },
    "later-validation": {
        "min_state": "Present",
        "title": "固化的资产缺少纵向验证，可能已过时",
        "artifact": "benchmark / 复盘记录",
        "fix": "定期对已固化资产做纵向验证（benchmark/复盘），过时资产标记并更新。",
        "output": ["资产有验证时间戳", "过时资产被标记"],
    },
}


def _RANK_VAL_INDEX(state: str) -> int:
    """证据状态 → 排序索引（供生成发现时比较等级）。"""
    order = ("Missing", "Present", "Wired", "Exercised", "Outcome-supported")
    return order.index(state) if state in order else 0


def generate_findings(items: list[ItemEvidence]) -> list[Finding]:
    """根据证据差距生成发现。仅当证据状态低于该检查项最低要求时生成。"""
    findings: list[Finding] = []
    for it in items:
        rule = _GAP_RULES.get(it.item)
        if rule is None:
            continue
        rank_now = _RANK_VAL_INDEX(it.state)
        rank_min = _RANK_VAL_INDEX(rule["min_state"])
        if rank_now >= rank_min:
            continue
        findings.append(Finding(
            id=f"{it.dimension}-{it.item}-gap",
            title=rule["title"],
            severity="Medium" if rank_now <= 1 else "Low",
            reason=(f"检查项 [{it.item}] 当前证据状态为 {it.state}（rank {rank_now}），"
                    f"低于该检查项最低要求 {rule['min_state']}。"),
            dimensionRefs=[it.dimension],
            itemRefs=[it.item],
            aiFixPrompt=rule["fix"],
            expectedArtifact=rule["artifact"],
            expectedOutput=rule["output"],
        ))
    # 按严重性排序
    findings.sort(key=lambda f: SEVERITY_RANK.get(f.severity, 0), reverse=True)
    return findings
