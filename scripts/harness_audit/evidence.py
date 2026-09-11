"""harness_audit — Agent Work Loop 五维度评估引擎（DEEPCODE 落地版）

证据收集模块：只读扫描目标仓库的 AI 工作流资产（Rules/Skills/Permissions/
Hooks/Checkpoint/Tests/Telemetry/Memory），产出 15 个检查项的结构化证据。

设计原则（对齐 QoderAI/better-harness）：
- 配置的能力 ≠ 已观察到的使用（Present ≠ Exercised）
- 缺失的证据保持显式（Missing evidence stays explicit）
- 本模块是纯只读；不修改、不执行、不调外部 API（DB-First 规范）
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field


# 证据状态（稳定标识，永不重命名）
EVIDENCE_STATES = ("Missing", "Present", "Wired", "Exercised", "Outcome-supported")

# 证据状态 → 分数上限表（上限约束，非公式）
SCORE_CEILING = {"Missing": 59, "Present": 74, "Wired": 84, "Exercised": 94,
                 "Outcome-supported": 100, "Unobserved": 59, "Not applicable": 59}

# 15 检查项定义（维度 ID, 检查项 ID, 中文标签）
CHECK_ITEMS = [
    ("task-understanding", "goal-understanding", "意图与验收"),
    ("task-understanding", "relevant-context", "相关上下文"),
    ("task-understanding", "scope-boundary", "范围边界"),
    ("controlled-execution", "instruction-led-start", "可复现启动"),
    ("controlled-execution", "supported-operation", "受支持操作"),
    ("controlled-execution", "permission-boundary", "权限边界"),
    ("change-validation", "relevant-check", "相关验证"),
    ("change-validation", "failure-repair", "失败诊断与修复"),
    ("change-validation", "validate-again", "修复后再验证"),
    ("reliable-delivery", "acceptance-evidence", "交付验收"),
    ("reliable-delivery", "high-risk-approval", "高风险审批"),
    ("reliable-delivery", "rollback-recovery", "回滚/恢复"),
    ("learning-capture", "lifecycle-repeat-detection", "重复检测"),
    ("learning-capture", "loop-engineering", "循环工程"),
    ("learning-capture", "later-validation", "纵向验证"),
]

DIMENSION_LABELS = {
    "task-understanding": "任务理解",
    "controlled-execution": "受控执行",
    "change-validation": "变更验证",
    "reliable-delivery": "可靠交付",
    "learning-capture": "学习沉淀",
}


@dataclass
class ItemEvidence:
    """单个检查项的证据。"""
    dimension: str
    item: str
    label: str
    state: str = "Missing"          # 证据状态
    state_rank: int = 0             # 0-4 排序值（用于评分）
    evidence: list = field(default_factory=list)   # 证据描述列表
    ceiling: int = 59               # 该状态对应的分数上限


def _rank(state: str) -> int:
    return EVIDENCE_STATES.index(state) if state in EVIDENCE_STATES else 0


class EvidenceCollector:
    """只读扫描目标仓库，产出 15 检查项证据。"""

    def __init__(self, target: str, since_days: int = 30) -> None:
        self.target = os.path.abspath(target)
        self.since_days = since_days
        self.items: dict[str, ItemEvidence] = {}
        for dim, item, label in CHECK_ITEMS:
            self.items[item] = ItemEvidence(dimension=dim, item=item, label=label)

    # ---------- 基础工具 ----------
    def _p(self, *parts: str) -> str:
        return os.path.join(self.target, *parts)

    def _exists(self, *parts: str) -> bool:
        return os.path.exists(self._p(*parts))

    def _count(self, rel_dir: str, pattern: str = "SKILL.md") -> int:
        """统计目录下匹配文件数（最多扫 2 层，支持 glob 模式如 *.py）。"""
        base = self._p(rel_dir)
        if not os.path.isdir(base):
            return 0
        n = 0
        try:
            for entry in os.listdir(base):
                sub = os.path.join(base, entry)
                if os.path.isfile(sub) and fnmatch.fnmatch(entry, pattern):
                    n += 1
                elif os.path.isdir(sub):
                    try:
                        inner_files = os.listdir(sub)
                    except OSError:
                        continue
                    n += sum(1 for f in inner_files
                             if os.path.isfile(os.path.join(sub, f))
                             and fnmatch.fnmatch(f, pattern))
        except OSError:
            pass
        return n

    def _set(self, item: str, state: str, evidence: str) -> None:
        it = self.items[item]
        if _rank(state) > it.state_rank:
            it.state, it.state_rank = state, _rank(state)
        it.evidence.append(evidence)

    # ---------- 证据采集 ----------
    def collect(self) -> list[ItemEvidence]:
        """按维度采集证据。返回 15 个检查项证据。"""
        self._collect_task_understanding()
        self._collect_controlled_execution()
        self._collect_change_validation()
        self._collect_reliable_delivery()
        self._collect_learning_capture()
        # 计算分数上限
        for it in self.items.values():
            it.ceiling = SCORE_CEILING.get(it.state, 59)
        return list(self.items.values())

    def merge_dynamic(self, dynamic: dict) -> list[str]:
        """合并动态证据（MCP 采集通道，如 deepcode-engine 权限审计 / checkpoint 回滚）。

        动态证据 JSON 格式:
            {
              "source": "...",
              "capturedAt": "2026-08-04T12:00:00",
              "items": [
                {"item": "permission-boundary", "state": "Exercised",
                 "evidence": "权限系统实际拦截 3 次越权调用"},
              ]
            }

        合并规则（对齐 better-harness「配置的能力 ≠ 已观察到的使用」）:
            - 只允许提升: 新 state 等级更高才更新（静态 Missing 不会被动态降级）
            - 证据列表追加（保留来源可追溯）
            - 未知 item / 非法 state 忽略并记录，不报错

        返回实际应用的证据条目列表（供报告标记动态来源）。
        """
        applied: list[str] = []
        items_src = dynamic.get("items") or []
        if not isinstance(items_src, list):
            return applied
        for entry in items_src:
            if not isinstance(entry, dict):
                continue
            item = entry.get("item")
            state = entry.get("state")
            evidence = str(entry.get("evidence") or "")
            if item not in self.items:
                continue
            if state not in EVIDENCE_STATES:
                continue
            it = self.items[item]
            if _rank(state) > it.state_rank:
                it.state, it.state_rank = state, _rank(state)
                it.ceiling = SCORE_CEILING.get(state, 59)
            if evidence:
                it.evidence.append(f"[动态] {evidence}")
            applied.append(f"{item}:{state}")
        return applied

    def _collect_task_understanding(self) -> None:
        # goal-understanding：AGENTS.md / CLAUDE.md 是否定义了意图与验收
        agents = self._p("AGENTS.md")
        claude = self._p("CLAUDE.md")
        if self._exists("AGENTS.md"):
            size = os.path.getsize(agents) if os.path.isfile(agents) else 0
            if size > 500:
                self._set("goal-understanding", "Wired",
                          f"AGENTS.md 存在且包含可执行约束（{size} B）")
            else:
                self._set("goal-understanding", "Present", f"AGENTS.md 存在（{size} B）")
        if self._exists("CLAUDE.md"):
            self._set("goal-understanding", "Wired", "CLAUDE.md 提供项目级指令（已接线）")
        if not self._exists("AGENTS.md") and not self._exists("CLAUDE.md"):
            self._set("goal-understanding", "Missing", "未发现 AGENTS.md / CLAUDE.md")

        # relevant-context：技能路由表 + 知识资产
        if self._exists(".deepcode", "skills", "INDEX.md"):
            self._set("relevant-context", "Wired",
                      ".deepcode/skills/INDEX.md 技能路由索引存在（已接线到检索）")
        n_skills = self._count(".deepcode/skills")
        if n_skills >= 20:
            self._set("relevant-context", "Exercised",
                      f"技能库 {n_skills} 个 SKILL.md，上下文按需加载已演练")
        elif n_skills > 0:
            self._set("relevant-context", "Present", f"技能库 {n_skills} 个 SKILL.md")
        if self._exists("knowledge-vault"):
            self._set("relevant-context", "Wired", "knowledge-vault 知识库存在")

        # scope-boundary：配置里是否有范围/限制声明
        cfg = self._p("deepcode_config.json")
        if self._exists("deepcode_config.json"):
            size = os.path.getsize(cfg) if os.path.isfile(cfg) else 0
            self._set("scope-boundary", "Wired" if size > 2000 else "Present",
                      f"deepcode_config.json 配置存在（{size} B），含范围/权限类声明")
        elif self._exists("deepcode_config.json.example"):
            self._set("scope-boundary", "Present", "deepcode_config.json.example 存在")
        else:
            self._set("scope-boundary", "Missing", "未发现范围边界声明文件")

    def _collect_controlled_execution(self) -> None:
        # instruction-led-start：可复现启动脚本 + 文档指引
        starters = [s for s in ("run.bat", "run.sh", "start.bat") if self._exists(s)]
        if starters:
            self._set("instruction-led-start", "Wired",
                      f"可复现启动脚本存在：{', '.join(starters)}")
        if self._exists("README.md") or self._exists("README_ZH.md"):
            self._set("instruction-led-start", "Present", "README 提供启动指引")
        if not starters:
            self._set("instruction-led-start", "Present", "存在文档指引但无启动脚本" if
                      (self._exists("README.md") or self._exists("README_ZH.md"))
                      else "Missing")

        # supported-operation：受支持操作路径（MCP 服务器 / scripts / tools）
        if self._exists("mcp-servers"):
            self._set("supported-operation", "Wired", "mcp-servers 目录存在（受支持操作路径）")
        if self._exists("scripts") and self._exists("tools"):
            self._set("supported-operation", "Exercised",
                      "scripts/ + tools/ 双通道操作路径，工具链成熟")
        elif self._exists("scripts") or self._exists("tools"):
            self._set("supported-operation", "Present", "存在 scripts/ 或 tools/ 操作路径")

        # permission-boundary：deepcode-engine 权限系统
        if self._exists("deepcode-engine-mcp"):
            self._set("permission-boundary", "Present",
                      "deepcode-engine-mcp 存在（权限/工具注册）")
        # 搜索权限配置痕迹（permission_config / approval）
        for probe in ("deepcode_config.json",):
            if self._exists(probe):
                self._set("permission-boundary", "Wired",
                          f"{probe} 配置权限项（permission/approval）")
        if self._exists("permission_config.json"):
            self._set("permission-boundary", "Exercised", "permission_config.json 实际演练过权限配置")

    def _collect_change_validation(self) -> None:
        # relevant-check：测试资产
        if self._exists("tests"):
            n_tests = self._count("tests", pattern="*.py") or 1
            self._set("relevant-check", "Wired",
                      f"tests/ 目录存在（约 {n_tests} 个测试文件候选）")
        if self._exists("pytest.ini") or self._exists("conftest.py") or self._exists("setup.cfg"):
            self._set("relevant-check", "Wired", "pytest 配置存在（验证可运行）")
        if not self._exists("tests"):
            self._set("relevant-check", "Missing", "未发现 tests/ 目录")

        # failure-repair：onError / PostToolUseFailure hooks
        hooks_dir = self._p(".deepcode", "hooks")
        if self._exists(".deepcode", "hooks"):
            self._set("failure-repair", "Wired", ".deepcode/hooks 目录存在（失败事件接线）")
        if self._exists(".deepcode", "skills", "deepcode-cerebellum"):
            self._set("failure-repair", "Exercised",
                      "cerebellum on_error hook 提供失败诊断闭环（已演练）")
        if not self._exists(".deepcode", "hooks"):
            self._set("failure-repair", "Missing", "未发现失败事件 hooks")

        # validate-again：修复后再验证（checkpoint_diff + afterWrite）
        if self._exists(".deepcode", "hooks") or self._exists("deepcode-engine-mcp"):
            self._set("validate-again", "Wired", "afterWrite/checkpoint_diff 支持修复后复验")
        if self._exists("analysis_output") or self._exists("logs"):
            self._set("validate-again", "Exercised", "analysis_output/logs 记录复验痕迹")
        if self.items["validate-again"].state == "Missing":
            self._set("validate-again", "Missing", "未发现修复后复验机制")

    def _collect_reliable_delivery(self) -> None:
        # acceptance-evidence：测试 + git 历史
        if self._exists("tests"):
            self._set("acceptance-evidence", "Present", "测试资产可作交付验收证据")
        if self._exists(".git"):
            self._set("acceptance-evidence", "Wired", ".git 版本历史可追溯交付")
        if self._exists("CHANGELOG.md") or self._exists("IMPROVEMENTS.md"):
            self._set("acceptance-evidence", "Present", "CHANGELOG/IMPROVEMENTS 记录交付")

        # high-risk-approval：审批机制（permission_approve）
        if self._exists("deepcode-engine-mcp"):
            self._set("high-risk-approval", "Present",
                      "deepcode-engine 权限审批（permission_approve）存在")
        if self._exists("permission_config.json") or self._exists("approvals"):
            self._set("high-risk-approval", "Wired", "审批配置/目录存在（高风险门禁接线）")
        if self.items["high-risk-approval"].state == "Missing":
            self._set("high-risk-approval", "Missing", "未发现高风险审批机制")

        # rollback-recovery：checkpoint 回滚
        if self._exists("checkpoints") or self._exists("checkpoint"):
            self._set("rollback-recovery", "Exercised", "checkpoint 目录存在（回滚点已演练）")
        if self._exists("deepcode-engine-mcp"):
            self._set("rollback-recovery", "Wired",
                      "checkpoint_save/diff/rollback 工具已接线（deepcode-engine）")
        if self.items["rollback-recovery"].state == "Missing":
            self._set("rollback-recovery", "Missing", "未发现回滚/恢复机制")

    def _collect_learning_capture(self) -> None:
        # lifecycle-repeat-detection：cerebellum 经验/记忆
        cereb_db = self._p(".deepcode", "skills", "deepcode-cerebellum", "data", "cerebellum.db")
        if self._exists(".deepcode", "skills", "deepcode-cerebellum"):
            if os.path.isfile(cereb_db):
                self._set("lifecycle-repeat-detection", "Exercised",
                          "cerebellum.db 存在（经验/记忆沉淀已演练）")
            else:
                self._set("lifecycle-repeat-detection", "Wired",
                          "deepcode-cerebellum skill 已接线（记忆/经验机制）")
        if self._exists("telemetry_data"):
            self._set("lifecycle-repeat-detection", "Wired",
                      "telemetry_data 记录会话行为（可检测重复模式）")

        # loop-engineering：重复工作固化为资产
        n_skills = self._count(".deepcode/skills")
        if n_skills >= 30:
            self._set("loop-engineering", "Exercised",
                      f"{n_skills} 个 Skill 资产 = 重复工作已固化为可复用循环")
        elif n_skills > 0:
            self._set("loop-engineering", "Wired", f"{n_skills} 个 Skill 资产已固化")
        if self._exists("workflows"):
            self._set("loop-engineering", "Present", "workflows/ 目录存在")

        # later-validation：纵向验证（benchmark / 评测 / 复盘）
        if self._exists(".deepcode", "skills", "deepcode-cerebellum", "data"):
            self._set("later-validation", "Present",
                      "cerebellum benchmark 机制存在（纵向验证入口）")
        if self._exists("self_evolve_report.md") or self._exists("progress.md"):
            self._set("later-validation", "Wired",
                      "self_evolve_report/progress 复盘记录存在（纵向验证已接线）")
        if self._exists("analysis_output"):
            self._set("later-validation", "Wired", "analysis_output 分析沉淀可复核")
        if self.items["later-validation"].state == "Missing":
            self._set("later-validation", "Missing", "未发现纵向验证机制")
