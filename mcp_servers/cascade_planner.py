#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CascadePlanner v1.0 — Windsurf Cascade 风格自动 Agent 规划循环
=============================================================
参考: Windsurf/Devin Desktop 的 Cascade 模式逆向分析

Cascade 的核心是 Plan → Act → Observe 循环:
  1. PLAN:  将用户任务分解为可执行步骤
  2. ACT:   执行工具调用（文件编辑/命令/搜索）
  3. OBSERVE: 检查结果，判断成功/失败
  4. ADAPT:  根据结果调整剩余计划
  5. LOOP:  重复直到所有步骤完成

与 DEEPCODE 现有组件集成:
  - SubAgentSystem: 子代理调度
  - tool_registry: 工具执行
  - parallel_executor: 并行步骤
  - signature_engine: 分析结果匹配

用法:
  planner = CascadePlanner()
  plan = planner.create_plan("分析这个二进制文件并找出漏洞")
  result = planner.execute(plan, on_step=lambda s: print(s.status))
"""

import asyncio
import json
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStep:
    """Cascade 规划中的一个步骤"""
    def __init__(self, id: str, action: str, params: Dict = None,
                 depends_on: List[str] = None,
                 tool: str = "", expected: str = ""):
        self.id = id
        self.action = action          # 操作描述
        self.params = params or {}    # 操作参数
        self.depends_on = depends_on or []
        self.tool = tool              # 要调用的工具名 ("", 留空表示 AI 推理)
        self.expected = expected      # 期望结果描述
        self.status = StepStatus.PENDING
        self.result: Any = None
        self.error: Optional[str] = None
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.retries = 0
        self.max_retries = 2

    @property
    def duration(self) -> float:
        if self.started_at and self.completed_at:
            s = datetime.fromisoformat(self.started_at)
            e = datetime.fromisoformat(self.completed_at)
            return (e - s).total_seconds()
        return 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "action": self.action,
            "tool": self.tool, "status": self.status.value,
            "depends_on": self.depends_on,
            "error": self.error,
            "duration": self.duration,
            "retries": self.retries,
        }


class CascadePlan:
    """完整的 Cascade 执行计划"""
    def __init__(self, goal: str):
        self.id = str(uuid.uuid4())[:8]
        self.goal = goal
        self.steps: List[PlanStep] = []
        self.created_at = datetime.now().isoformat()
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.status = "created"  # created/running/done/failed
        self.context: Dict = {}  # 跨步骤共享上下文

    def add_step(self, step: PlanStep):
        self.steps.append(step)
        return step

    @property
    def progress(self) -> dict:
        total = len(self.steps)
        done = sum(1 for s in self.steps if s.status in (
            StepStatus.SUCCESS, StepStatus.FAILED, StepStatus.SKIPPED))
        running = sum(1 for s in self.steps if s.status == StepStatus.RUNNING)
        return {
            "total": total, "done": done, "running": running,
            "pending": total - done - running,
            "pct": f"{done / max(total, 1) * 100:.0f}%",
        }

    def next_steps(self) -> List[PlanStep]:
        """获取可执行的下一步骤 (依赖已完成的步骤)"""
        ready = []
        for step in self.steps:
            if step.status != StepStatus.PENDING:
                continue
            deps_met = all(
                any(s.id == dep and s.status == StepStatus.SUCCESS
                    for s in self.steps)
                for dep in step.depends_on
            )
            if deps_met:
                ready.append(step)
        return ready


class CascadePlanner:
    """
    Windsurf Cascade 风格的自动规划循环引擎

    核心循环:
      plan() → execute_plan() → observe() → adapt() → loop
    """

    def __init__(self, sub_agent_system=None):
        self._sub_agent = sub_agent_system
        self._plans: Dict[str, CascadePlan] = {}
        self._on_step: Optional[Callable] = None
        self._stats = {
            "plans_created": 0,
            "plans_completed": 0,
            "plans_failed": 0,
            "steps_executed": 0,
            "steps_failed": 0,
            "adaptations": 0,
        }

    # ── 规划生成 (PLAN) ──

    def create_plan(self, goal: str,
                    predefined_steps: List[Dict] = None) -> CascadePlan:
        """
        创建 Cascade 执行计划

        Args:
            goal: 目标任务描述
            predefined_steps: 预定义步骤列表 (可选)
              [{"action": "...", "tool": "...", "params": {...}}, ...]

        Returns:
            CascadePlan 对象
        """
        plan = CascadePlan(goal)

        if predefined_steps:
            for i, step_def in enumerate(predefined_steps):
                plan.add_step(PlanStep(
                    id=f"step_{i}",
                    action=step_def.get("action", f"Step {i}"),
                    tool=step_def.get("tool", ""),
                    params=step_def.get("params", {}),
                    depends_on=step_def.get("depends_on", []),
                ))
        else:
            # 自动生成步骤 (基于任务类型模板)
            plan = self._auto_generate_plan(goal)

        self._plans[plan.id] = plan
        self._stats["plans_created"] += 1
        return plan

    def _auto_generate_plan(self, goal: str) -> CascadePlan:
        """根据任务类型自动生成计划步骤"""
        goal_lower = goal.lower()
        plan = CascadePlan(goal)

        # 二进制分析类任务
        if any(kw in goal_lower for kw in ["分析", "逆向", "二进制", "analyze", "reverse", "binary"]):
            plan.add_step(PlanStep("step_0", "识别文件格式和架构", tool="ghidra-mcp.get_metadata"))
            plan.add_step(PlanStep("step_1", "扫描函数列表", tool="ghidra-mcp.list_functions",
                                    depends_on=["step_0"]))
            plan.add_step(PlanStep("step_2", "FLIRT 签名匹配识别库函数",
                                    tool="ida_bridge.flirt_scan", depends_on=["step_1"]))
            plan.add_step(PlanStep("step_3", "反编译关键函数", tool="ghidra-mcp.decompile_function",
                                    depends_on=["step_2"]))
            plan.add_step(PlanStep("step_4", "PCode 微码分析", tool="ida_bridge.analyze_pcode",
                                    depends_on=["step_3"]))
            plan.add_step(PlanStep("step_5", "API 调用链检测", tool="ida_bridge.detect_chains",
                                    depends_on=["step_4"]))
            plan.add_step(PlanStep("step_6", "生成分析报告", depends_on=["step_5"]))

        # 漏洞分析类任务
        elif any(kw in goal_lower for kw in ["漏洞", "vuln", "cve", "安全", "security"]):
            plan.add_step(PlanStep("step_0", "扫描函数列表", tool="ghidra-mcp.list_functions"))
            plan.add_step(PlanStep("step_1", "识别可疑 API 调用",
                                    tool="ghidra-mcp.detect_malware_behaviors",
                                    depends_on=["step_0"]))
            plan.add_step(PlanStep("step_2", "PCode 调用链分析",
                                    tool="ida_bridge.detect_chains",
                                    depends_on=["step_1"]))
            plan.add_step(PlanStep("step_3", "生成漏洞报告", depends_on=["step_2"]))

        # 通用任务
        else:
            plan.add_step(PlanStep("step_0", f"理解任务: {goal[:50]}"))
            plan.add_step(PlanStep("step_1", "收集所需信息", depends_on=["step_0"]))
            plan.add_step(PlanStep("step_2", "执行分析", depends_on=["step_1"]))
            plan.add_step(PlanStep("step_3", "整合结果并输出", depends_on=["step_2"]))

        return plan

    # ── 执行循环 (ACT → OBSERVE → ADAPT) ──

    async def execute(self, plan: CascadePlan,
                      tool_executor: Callable = None,
                      on_step: Callable = None) -> CascadePlan:
        """
        执行 Cascade 循环 (核心方法)

        Args:
            plan: 要执行的计划
            tool_executor: 工具执行回调 async(name, params) -> result
            on_step: 步骤状态变更回调

        Returns:
            执行完成的计划
        """
        self._on_step = on_step or self._default_on_step
        plan.status = "running"
        plan.started_at = datetime.now().isoformat()

        while True:
            # 获取可执行的步骤
            ready = plan.next_steps()
            if not ready:
                break

            # 并发执行可并行步骤
            tasks = []
            for step in ready:
                tasks.append(self._execute_step(step, plan, tool_executor))
            await asyncio.gather(*tasks)

        # 检查完成状态
        plan.completed_at = datetime.now().isoformat()
        failed = any(s.status == StepStatus.FAILED for s in plan.steps)
        plan.status = "failed" if failed else "done"

        if failed:
            self._stats["plans_failed"] += 1
        else:
            self._stats["plans_completed"] += 1

        return plan

    async def _execute_step(self, step: PlanStep, plan: CascadePlan,
                             tool_executor: Callable = None):
        """执行单个 Cascade 步骤 (ACT + OBSERVE)"""
        step.status = StepStatus.RUNNING
        step.started_at = datetime.now().isoformat()
        self._stats["steps_executed"] += 1

        try:
            if step.tool and tool_executor:
                # ACT: 调用工具
                result = await tool_executor(step.tool, step.params)
                step.result = result
                step.status = StepStatus.SUCCESS

                # OBSERVE: 检查结果
                if result is None or (isinstance(result, dict) and result.get("error")):
                    step.status = StepStatus.FAILED
                    step.error = str(result.get("error", "Empty result"))

            else:
                # 纯推理步骤 (AI 驱动)
                step.status = StepStatus.SUCCESS
                step.result = {"note": f"AI推理步骤: {step.action}"}

        except Exception as e:
            step.error = str(e)
            if step.retries < step.max_retries:
                step.retries += 1
                self._stats["steps_failed"] += 1
                return await self._execute_step(step, plan, tool_executor)

            step.status = StepStatus.FAILED
            self._stats["steps_failed"] += 1

            # ADAPT: 失败时尝试调整
            self._adapt_plan(plan, step)

        step.completed_at = datetime.now().isoformat()

        if self._on_step:
            self._on_step(step, plan)

    def _adapt_plan(self, plan: CascadePlan, failed_step: PlanStep):
        """
        根据失败步骤调整计划 (ADAPT)

        Windsurf Cascade 在步骤失败时会:
          1. 标记依赖该步骤的后续步骤为 SKIPPED
          2. 记录失败原因到 plan.context
          3. 可选: 插入恢复步骤
        """
        self._stats["adaptations"] += 1
        plan.context["last_failure"] = {
            "step_id": failed_step.id,
            "action": failed_step.action,
            "error": failed_step.error,
            "adapted_at": datetime.now().isoformat(),
        }

        # 标记依赖失败的后续步骤
        for step in plan.steps:
            if failed_step.id in step.depends_on:
                step.status = StepStatus.SKIPPED
                step.error = f"依赖步骤 {failed_step.id} 失败"

    def _default_on_step(self, step: PlanStep, plan: CascadePlan):
        """默认步骤回调"""
        pass

    # ── 查询 ──

    def get_plan(self, plan_id: str) -> Optional[CascadePlan]:
        return self._plans.get(plan_id)

    def get_plan_progress(self, plan_id: str) -> Optional[dict]:
        plan = self._plans.get(plan_id)
        if not plan:
            return None
        steps_detail = [s.to_dict() for s in plan.steps]
        return {
            "plan_id": plan.id,
            "goal": plan.goal[:60],
            "status": plan.status,
            "progress": plan.progress,
            "steps": steps_detail,
        }

    def list_plans(self) -> List[dict]:
        return [{
            "id": p.id,
            "goal": p.goal[:50],
            "status": p.status,
            "progress": p.progress,
        } for p in self._plans.values()]

    def get_stats(self) -> dict:
        return dict(self._stats)
