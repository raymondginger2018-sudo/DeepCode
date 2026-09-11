#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto-Spawn Scheduler — 自动分身调度器
======================================
基于 hooks 事件 + 每日更新流水线，自动派生 Agent 分身并**实际执行**任务。

触发场景 (稳健模式默认开启 3 个):
  1. complex_task     复杂任务        → coder      (任务含 ≥N 次工具调用 / 涉及 ≥N 文件)
  2. failure_recovery 失败自愈        → reviewer   (同一任务连续失败 ≥N 次)
  3. daily_review     每日复盘        → researcher (DAILY AUTO UPDATE 完成后)
  4. parallel_split   并行分解(默认关) → coordinator (≥N 个独立子目标)
  5. long_running     长耗时(默认关)  → shell_executor (运行 >N 分钟未完成)

防护机制:
  - 全局开关 / 每日配额 / 每小时配额 / 嵌套深度 ≤3 / 防抖去重 / 失败降级 / 全量审计

执行通道:
  - 线程记录: agent_thread_manager.spawn() → database.db (agent_threads)
  - 实际执行: python -m cli.exec_cli -- <prompt> (headless 子 DeepCode 进程, 后台运行)

用法:
  python auto_spawn.py evaluate '{"task": "...", "tool_calls": 6, "files": ["a.py","b.py","c.py"]}'
  python auto_spawn.py spawn '{"scenario": "daily_review", "task": "..."}' --force
  python auto_spawn.py status
  python auto_spawn.py hook --event postTask --ctx '{"task": "...", "tool_calls": 8}'
  python auto_spawn.py stats
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

SKILL_DIR = Path(__file__).parent
DATA_DIR = SKILL_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_FILE = SKILL_DIR / "auto_spawn_config.json"
STATE_DB = DATA_DIR / "auto_spawn.db"
LOG_FILE = DATA_DIR / "auto_spawn.log"
WORKERS_ROOT = Path("F:/DEEPCODE/.workers")

# 分身可执行的任务模板
PROMPT_TEMPLATES = {
    "complex_task": (
        "你是 {agent_type} 分身 (自动派生)。父任务「{task}」被判定为复杂任务"
        "(工具调用 {tool_calls} 次 / 涉及 {files} 个文件)，请独立完成该任务并返回结果摘要。"
    ),
    "failure_recovery": (
        "你是 reviewer 分身 (自动派生)。父任务「{task}」连续失败 {fail_count} 次，"
        "请审查失败原因，给出具体修复建议，必要时直接修复。"
    ),
    "daily_review": (
        "你是 researcher 分身 (自动派生)。今日 DAILY AUTO UPDATE 已完成。\n"
        "任务: 生成当日 A 股复盘报告。\n"
        "数据位置: 1) SQLite 库 F:/DEEPCODE/DeepCode (CLONE)/quant_trading/kline_cache.db "
        "(表 daily_review_meta/daily_ladder/daily_sector/engine_scores); "
        "2) 知识库 F:/DEEPCODE/.deepcode/skills/deepcode-knowledge/data/vault/notes/。\n"
        "报告输出: 写入 F:/DEEPCODE/DeepCode (CLONE)/quant_trading/reports/ 下 "
        "review_YYYYMMDD.md，内容含市场温度/板块温度/连板梯队/GRPO摘要四节。\n"
        "直接用 python/bash 读取数据，禁止无关探索，3 轮内完成。"
    ),
    "parallel_split": (
        "你是 coordinator 分身 (自动派生)。父任务「{task}」包含 {n} 个独立子目标，"
        "请拆解并协调完成。"
    ),
    "long_running": (
        "你是 shell_executor 分身 (自动派生)。父任务「{task}」运行超过 {minutes} 分钟未完成，"
        "请接管继续执行并汇报进度。"
    ),
}

DEFAULT_CONFIG: Dict = {
    "enabled": True,
    "daily_quota": 5,
    "hourly_quota": 3,
    "max_depth": 3,
    "debounce_minutes": 30,
    "max_iterations": 20,
    "timeout": 600,
    "exec_enabled": True,          # 实际执行开关 (False=仅记录线程)
    # 熔断降级 (对标 Circuit Breaker): 连续失败 N 次 → 冷却期内熔断 (仅记录不执行)
    "circuit_breaker": {
        "enabled": True,
        "max_consecutive_failures": 3,
        "cooldown_minutes": 60,
    },
    # Handoff 闭环: 分身成功后把结果摘要写回知识库
    "handoff": {
        "enabled": True,
        "vault_dir": "F:/DEEPCODE/.deepcode/skills/deepcode-knowledge/data/vault/notes",
    },
    "scenarios": {
        "complex_task":     {"enabled": True,  "tool_calls_threshold": 5, "files_threshold": 3,
                             "token_threshold": 20000, "agent_type": "coder"},
        "failure_recovery": {"enabled": True,  "fail_count": 2, "fail_rate": 0.5, "min_tasks": 3,
                             "agent_type": "reviewer"},
        "daily_review":     {"enabled": True,  "agent_type": "researcher"},
        "parallel_split":   {"enabled": False, "subtasks_threshold": 3, "agent_type": "coordinator"},
        "long_running":     {"enabled": False, "minutes": 10, "agent_type": "shell_executor"},
    },
}


# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config() -> Dict:
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            # 合并默认值 (缺省字段补齐)
            merged = {**DEFAULT_CONFIG, **cfg}
            merged["scenarios"] = {**DEFAULT_CONFIG["scenarios"], **cfg.get("scenarios", {})}
            return merged
        except Exception as e:
            log(f"[config] 配置解析失败 ({e})，使用默认配置")
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: Dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_conn():
    conn = sqlite3.connect(str(STATE_DB), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS spawn_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            scenario TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            thread_id TEXT,
            task TEXT,
            reason TEXT,
            status TEXT DEFAULT 'spawned',   -- spawned/running/completed/failed/skipped
            exit_code INTEGER,
            detail TEXT
        );
        CREATE TABLE IF NOT EXISTS counters (
            kind TEXT PRIMARY KEY,           -- daily_YYYYMMDD / hourly_YYYYMMDDHH
            count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS debounce (
            signature TEXT PRIMARY KEY,
            last_ts REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS failure_counts (
            signature TEXT PRIMARY KEY,
            fail_count INTEGER DEFAULT 0,
            last_ts REAL NOT NULL
        );
    """)
    conn.commit()
    conn.close()


def _counter_get(kind: str) -> int:
    conn = _get_conn()
    r = conn.execute("SELECT count FROM counters WHERE kind=?", (kind,)).fetchone()
    conn.close()
    return r[0] if r else 0


def _counter_inc(kind: str):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO counters(kind, count) VALUES(?, 1) "
        "ON CONFLICT(kind) DO UPDATE SET count=count+1",
        (kind,))
    conn.commit()
    conn.close()


def _debounce_get(sig: str) -> Optional[float]:
    conn = _get_conn()
    r = conn.execute("SELECT last_ts FROM debounce WHERE signature=?", (sig,)).fetchone()
    conn.close()
    return r[0] if r else None


def _debounce_set(sig: str):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO debounce(signature, last_ts) VALUES(?, ?) "
        "ON CONFLICT(signature) DO UPDATE SET last_ts=excluded.last_ts",
        (sig, time.time()))
    conn.commit()
    conn.close()


def _failure_get(sig: str) -> int:
    conn = _get_conn()
    r = conn.execute("SELECT fail_count FROM failure_counts WHERE signature=?", (sig,)).fetchone()
    conn.close()
    return r[0] if r else 0


def _failure_inc(sig: str) -> int:
    conn = _get_conn()
    conn.execute(
        "INSERT INTO failure_counts(signature, fail_count, last_ts) VALUES(?, 1, ?) "
        "ON CONFLICT(signature) DO UPDATE SET fail_count=fail_count+1, last_ts=excluded.last_ts",
        (sig, time.time()))
    conn.commit()
    cnt = conn.execute("SELECT fail_count FROM failure_counts WHERE signature=?", (sig,)).fetchone()[0]
    conn.close()
    return cnt


def _audit(**kw):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO spawn_log(ts, scenario, agent_type, thread_id, task, reason, status, exit_code, detail) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (time.time(), kw.get("scenario", ""), kw.get("agent_type", ""), kw.get("thread_id", ""),
         kw.get("task", ""), kw.get("reason", ""), kw.get("status", "spawned"),
         kw.get("exit_code"), kw.get("detail", "")))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════
# 熔断降级 (Circuit Breaker) + Handoff 闭环
# ═══════════════════════════════════════════

def circuit_breaker_state(cfg: Dict) -> Dict:
    """熔断状态: 统计当天连续失败次数, 返回 {open, consecutive_failures, reason}"""
    cb = cfg.get("circuit_breaker", {})
    if not cb.get("enabled", True):
        return {"open": False, "consecutive_failures": 0}
    threshold = cb.get("max_consecutive_failures", 3)
    cooldown_min = cb.get("cooldown_minutes", 60)

    conn = _get_conn()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = conn.execute(
        "SELECT ts, status FROM spawn_log WHERE ts >= ? ORDER BY ts DESC LIMIT 100",
        (today_start,)).fetchall()
    conn.close()

    # 统计从最新往前的连续 failed
    consecutive = 0
    last_fail_ts = None
    for ts, status in rows:
        if status == "failed":
            consecutive += 1
            if last_fail_ts is None:
                last_fail_ts = ts
        elif status in ("completed", "spawned") and consecutive > 0:
            break  # 成功打断连续失败
    if consecutive == 0:
        return {"open": False, "consecutive_failures": 0}

    if consecutive >= threshold:
        # 冷却期内熔断; 冷却期外 HALF_OPEN 放行探测
        cooled = (time.time() - last_fail_ts) >= cooldown_min * 60
        if not cooled:
            return {"open": True, "consecutive_failures": consecutive,
                    "reason": f"连续失败 {consecutive} 次 ≥ {threshold}, 冷却中 (还需 {cooldown_min - int((time.time()-last_fail_ts)/60)} 分钟)"}
    return {"open": False, "consecutive_failures": consecutive}


def handoff_result(scenario: str, agent_type: str, thread_id: str, final_text: str,
                   cfg: Dict) -> Optional[str]:
    """Handoff 闭环: 分身成功后把结果摘要写回知识库 (对标 OpenAI Handoff)"""
    hf = cfg.get("handoff", {})
    if not hf.get("enabled", True):
        return None
    vault = Path(hf.get("vault_dir", ""))
    try:
        vault.mkdir(parents=True, exist_ok=True)
        ts = datetime.now()
        date_str = ts.strftime("%Y%m%d")
        # 提取 final_text 摘要 (截断)
        summary = (final_text or "").strip()[:1500] or "(分身完成但无文本输出)"
        note = (
            f"---\n"
            f"title: 分身结果 {scenario} {ts.strftime('%Y-%m-%d %H:%M')}\n"
            f"date: {ts.strftime('%Y-%m-%d')}\n"
            f"type: note\n"
            f"tags: [分身, auto-spawn, {scenario}]\n"
            f"---\n\n"
            f"# 分身执行结果 ({scenario}/{agent_type})\n\n"
            f"- 线程: {thread_id or 'N/A'}\n"
            f"- 时间: {ts.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"## 结果摘要\n\n{summary}\n"
        )
        fpath = vault / f"spawn_handoff_{scenario}_{date_str}_{(thread_id or 'x')[:8]}.md"
        fpath.write_text(note, encoding="utf-8")
        return str(fpath)
    except Exception as e:
        log(f"[handoff] 写回知识库失败: {e}")
        return None


# ═══════════════════════════════════════════
# 防护检查 (GuardRail)
# ═══════════════════════════════════════════

class GuardRail:
    """配额/深度/防抖/开关防护"""

    def __init__(self, cfg: Dict, parent_depth: int = 0):
        self.cfg = cfg
        self.parent_depth = parent_depth

    def check(self, signature: str) -> Optional[str]:
        """返回 None=放行, 字符串=拒绝原因"""
        if not self.cfg.get("enabled", True):
            return "全局开关已关闭"
        now = datetime.now()
        # 熔断检查 (连续失败 → 冷却期熔断)
        cb = circuit_breaker_state(self.cfg)
        if cb.get("open"):
            return f"熔断中: {cb.get('reason')}"
        # 每日配额
        daily_key = f"daily_{now.strftime('%Y%m%d')}"
        if _counter_get(daily_key) >= self.cfg.get("daily_quota", 5):
            return f"超过每日配额 ({self.cfg.get('daily_quota')})"
        # 每小时配额
        hourly_key = f"hourly_{now.strftime('%Y%m%d%H')}"
        if _counter_get(hourly_key) >= self.cfg.get("hourly_quota", 3):
            return f"超过每小时配额 ({self.cfg.get('hourly_quota')})"
        # 深度限制
        if self.parent_depth >= self.cfg.get("max_depth", 3):
            return f"达到最大嵌套深度 ({self.cfg.get('max_depth')})"
        # 防抖去重
        debounce_min = self.cfg.get("debounce_minutes", 30)
        last = _debounce_get(signature)
        if last and (time.time() - last) < debounce_min * 60:
            return f"防抖期内重复触发 ({int(debounce_min)} 分钟内不重复)"
        return None


# ═══════════════════════════════════════════
# 场景规则匹配
# ═══════════════════════════════════════════

def _signature(scenario: str, task: str) -> str:
    """任务签名: 场景+任务前80字符(规范化)"""
    t = re.sub(r"\s+", " ", (task or "")).strip()[:80]
    return f"{scenario}:{t}"


def match_scenario(ctx: Dict) -> Optional[Dict]:
    """根据任务上下文匹配触发场景, 返回 {scenario, agent_type, reason, prompt} 或 None"""
    cfg = load_config()
    sc = cfg["scenarios"]
    task = str(ctx.get("task", ""))[:300]

    # 1. 复杂任务 (多维指标: 工具调用 / 文件数 / token 消耗)
    if sc["complex_task"]["enabled"]:
        tool_calls = int(ctx.get("tool_calls", 0) or 0)
        files = ctx.get("files") or []
        token_usage = int(ctx.get("token_usage", 0) or 0)
        t_tool = sc["complex_task"].get("tool_calls_threshold", 5)
        t_file = sc["complex_task"].get("files_threshold", 3)
        t_token = sc["complex_task"].get("token_threshold", 20000)
        if tool_calls >= t_tool or len(files) >= t_file or token_usage >= t_token:
            at = sc["complex_task"]["agent_type"]
            return {
                "scenario": "complex_task", "agent_type": at,
                "reason": f"复杂任务 (工具调用{tool_calls}次/文件{len(files)}个/token{token_usage} ≥ 阈值{t_tool}/{t_file}/{t_token})",
                "prompt": PROMPT_TEMPLATES["complex_task"].format(
                    agent_type=at, task=task, tool_calls=tool_calls, files=len(files)),
            }

    # 2. 失败自愈 (多维指标: 连续失败次数 / 失败率)
    if sc["failure_recovery"]["enabled"]:
        fail_count = int(ctx.get("fail_count", 0) or 0)
        threshold = sc["failure_recovery"].get("fail_count", 2)
        fail_rate = float(ctx.get("fail_rate", 0) or 0)
        tasks_total = int(ctx.get("tasks_total", 0) or 0)
        min_tasks = int(sc["failure_recovery"].get("min_tasks", 3))
        rate_threshold = float(sc["failure_recovery"].get("fail_rate", 0.5))
        matched = fail_count >= threshold or (tasks_total >= min_tasks and fail_rate >= rate_threshold)
        if matched:
            at = sc["failure_recovery"]["agent_type"]
            return {
                "scenario": "failure_recovery", "agent_type": at,
                "reason": f"失败自愈 (连续失败{fail_count}次≥{threshold} 或 失败率{fail_rate:.0%}≥{rate_threshold:.0%} 任务{tasks_total}≥{min_tasks})",
                "prompt": PROMPT_TEMPLATES["failure_recovery"].format(
                    agent_type=at, task=task, fail_count=max(fail_count, threshold)),
            }

    # 3. 每日复盘
    if sc["daily_review"]["enabled"] and ctx.get("scenario") == "daily_review":
        at = sc["daily_review"]["agent_type"]
        return {
            "scenario": "daily_review", "agent_type": at,
            "reason": "DAILY AUTO UPDATE 完成 → 自动派生复盘",
            "prompt": PROMPT_TEMPLATES["daily_review"].format(agent_type=at),
        }

    # 4. 并行分解 (默认关)
    if sc["parallel_split"]["enabled"]:
        subtasks = ctx.get("subtasks") or []
        threshold = sc["parallel_split"].get("subtasks_threshold", 3)
        if len(subtasks) >= threshold:
            at = sc["parallel_split"]["agent_type"]
            return {
                "scenario": "parallel_split", "agent_type": at,
                "reason": f"检测到 {len(subtasks)} 个独立子目标 ≥ {threshold}",
                "prompt": PROMPT_TEMPLATES["parallel_split"].format(
                    agent_type=at, task=task, n=len(subtasks)),
            }

    # 5. 长耗时 (默认关)
    if sc["long_running"]["enabled"]:
        minutes = float(ctx.get("elapsed_minutes", 0) or 0)
        threshold = sc["long_running"].get("minutes", 10)
        if minutes >= threshold:
            at = sc["long_running"]["agent_type"]
            return {
                "scenario": "long_running", "agent_type": at,
                "reason": f"运行 {minutes:.0f} 分钟 ≥ {threshold} 分钟未完成",
                "prompt": PROMPT_TEMPLATES["long_running"].format(
                    agent_type=at, task=task, minutes=int(minutes)),
            }

    return None


# ═══════════════════════════════════════════
# 派生 + 实际执行
# ═══════════════════════════════════════════

def spawn_and_execute(decision: Dict, cfg: Dict, parent_thread_id: str = None,
                      parent_depth: int = 0) -> Dict:
    """记录线程 + 后台实际执行"""
    scenario = decision["scenario"]
    agent_type = decision["agent_type"]
    task = decision.get("task", "")
    prompt = decision["prompt"]

    # 1. 记录线程 (agent_thread_manager)
    thread_id = None
    try:
        sys.path.insert(0, str(SKILL_DIR))
        from agent_thread_manager import AgentThreadManager
        mgr = AgentThreadManager()
        thread = mgr.spawn(
            goal=prompt[:200],
            agent_type=agent_type,
            parent_thread_id=parent_thread_id,
            context={"auto_spawn": True, "scenario": scenario},
        )
        thread_id = thread.id
        log(f"[spawn] 线程记录: {thread_id} ({agent_type}) 场景={scenario}")
    except Exception as e:
        log(f"[spawn] 线程记录失败: {e} (继续执行不阻塞)")

    # 2. 计数 + 防抖标记
    now = datetime.now()
    _counter_inc(f"daily_{now.strftime('%Y%m%d')}")
    _counter_inc(f"hourly_{now.strftime('%Y%m%d%H')}")
    sig = _signature(scenario, task)
    _debounce_set(sig)

    # 3. 审计
    _audit(scenario=scenario, agent_type=agent_type, thread_id=thread_id,
           task=task[:200], reason=decision["reason"], status="spawned")

    # 4. 实际执行 (独立子进程, 不依赖父进程生命周期)
    if cfg.get("exec_enabled", True):
        try:
            payload = json.dumps({
                "prompt": prompt, "thread_id": thread_id,
                "scenario": scenario, "agent_type": agent_type,
                "timeout": cfg.get("timeout", 600),
                "max_iterations": cfg.get("max_iterations", 20),
                "model": cfg.get("model", "deepseek-v4-flash"),
            }, ensure_ascii=False)
            subprocess.Popen(
                [sys.executable, str(SKILL_DIR / "auto_spawn.py"), "exec", payload],
                cwd="F:/DEEPCODE",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            exec_status = "running(独立进程)"
        except Exception as e:
            log(f"[spawn] 执行器启动失败: {e}")
            exec_status = f"exec-fail: {e}"
    else:
        exec_status = "record-only"

    return {"thread_id": thread_id, "scenario": scenario, "agent_type": agent_type,
            "reason": decision["reason"], "execution": exec_status}


def _execute_worker(prompt: str, thread_id: Optional[str], scenario: str,
                    agent_type: str, cfg: Dict):
    """后台执行: python -m cli.exec_cli -- <prompt> (headless 子 DeepCode 进程)"""
    ws = WORKERS_ROOT / f"spawn-{thread_id or int(time.time())}"
    ws.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "cli.exec_cli", prompt,
        "--workspace", str(ws), "--json",
        "--model", cfg.get("model", "deepseek-v4-flash"),
        "--max-iterations", str(cfg.get("max_iterations", 20)),
    ]
    env = dict(os.environ)
    env["DEEPCODE_AGENT_DEPTH"] = "1"
    env["DEEPCODE_AGENT_ALLOW_SPAWN"] = "0"
    env["PYTHONPATH"] = os.environ.get("PYTHONPATH", "") + os.pathsep + "F:/DEEPCODE"
    env["PYTHONIOENCODING"] = "utf-8"

    start = time.time()
    out_file = DATA_DIR / f"exec_{thread_id or int(time.time())}.log"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.get("timeout", 600),
                           cwd="F:/DEEPCODE", env=env, shell=False,
                           encoding="utf-8", errors="replace")
        elapsed = time.time() - start
        # max_iterations 是软上限: 达到后任务正常完成 (stdout 有 task_complete) 但 exit=1,
        # 仅凭 returncode 会误判 failed → handoff 永不触发 → "跑完不汇报"
        out = r.stdout or ""
        done = r.returncode == 0 or '"type": "task_complete"' in out or '"stop_reason": "max_iterations"' in out
        status = "completed" if done else "failed"
        # 输出落盘便于诊断
        try:
            out_file.write_text(
                f"=== spawn {scenario}/{agent_type} thread={thread_id} exit={r.returncode} elapsed={elapsed:.0f}s ===\n"
                f"[stdout]\n{(r.stdout or '')[:4000]}\n[stderr]\n{(r.stderr or '')[:2000]}\n",
                encoding="utf-8")
        except Exception:
            pass
        _audit(scenario=scenario, agent_type=agent_type, thread_id=thread_id,
               task=prompt[:100], status=status, exit_code=r.returncode,
               detail=f"elapsed={elapsed:.0f}s stdout={(r.stdout or '')[:80].replace(chr(10),' ')}")
        log(f"[exec] 分身执行完成: {status} (exit={r.returncode}, {elapsed:.0f}s, {scenario}/{agent_type})")
        # Handoff 闭环: 分身成功 → 提取 final_text 写回知识库 (对标 OpenAI Handoff)
        if status == "completed":
            final_text = ""
            try:
                for line in (r.stdout or "").splitlines():
                    if '"type": "task_complete"' in line or '"type": "final_text"' in line:
                        try:
                            ev = json.loads(line)
                            final_text = ev.get("msg", {}).get("final_text", "") or final_text
                        except Exception:
                            pass
            except Exception:
                pass
            handoff_path = handoff_result(scenario, agent_type, thread_id, final_text, cfg)
            if handoff_path:
                log(f"[handoff] 分身结果已写回知识库: {handoff_path}")
                _audit(scenario=scenario, agent_type=agent_type, thread_id=thread_id,
                       task=prompt[:100], status="handoff", exit_code=0,
                       detail=f"handoff→{handoff_path}")
        # 更新线程状态
        if thread_id:
            try:
                sys.path.insert(0, str(SKILL_DIR))
                from agent_thread_manager import AgentThreadManager
                mgr = AgentThreadManager()
                mgr.update_status(thread_id, status)
            except Exception:
                pass
    except subprocess.TimeoutExpired:
        _audit(scenario=scenario, agent_type=agent_type, thread_id=thread_id,
               task=prompt[:100], status="failed", exit_code=-1,
               detail=f"timeout {cfg.get('timeout')}s")
        log(f"[exec] 分身执行超时 ({cfg.get('timeout')}s)，已终止")
        if thread_id:
            try:
                sys.path.insert(0, str(SKILL_DIR))
                from agent_thread_manager import AgentThreadManager
                mgr = AgentThreadManager()
                mgr.update_status(thread_id, "failed", error="timeout")
            except Exception:
                pass
    except Exception as e:
        _audit(scenario=scenario, agent_type=agent_type, thread_id=thread_id,
               task=prompt[:100], status="failed", detail=f"error: {e}")
        log(f"[exec] 分身启动失败: {e} (主任务不受影响)")


# ═══════════════════════════════════════════
# 主入口: evaluate (决策 + 派生)
# ═══════════════════════════════════════════

def evaluate(ctx: Dict, parent_thread_id: str = None, parent_depth: int = 0,
             force: bool = False) -> Dict:
    """核心决策入口: 匹配场景 → 防护检查 → 派生执行"""
    _init_db()
    cfg = load_config()

    if not force and not cfg.get("enabled", True):
        return {"action": "skip", "reason": "全局开关已关闭"}

    decision = match_scenario(ctx)
    if not decision:
        return {"action": "skip", "reason": "无匹配的触发场景"}

    decision["task"] = str(ctx.get("task", ""))
    sig = _signature(decision["scenario"], decision["task"])

    if not force:
        guard = GuardRail(cfg, parent_depth)
        denied = guard.check(sig)
        if denied:
            # 熔断 → 降级为"仅记录" (对标 Circuit Breaker 降级, 不实际执行但保留线索)
            cb = circuit_breaker_state(cfg)
            if cb.get("open"):
                degraded_cfg = {**cfg, "exec_enabled": False}
                result = spawn_and_execute(decision, degraded_cfg, parent_thread_id, parent_depth)
                log(f"[degraded] {decision['scenario']}: 熔断降级为仅记录 ({cb.get('reason')})")
                return {"action": "degraded", "reason": cb.get("reason"), **result}
            _audit(scenario=decision["scenario"], agent_type=decision["agent_type"],
                   task=decision["task"][:200], status="skipped", detail=denied)
            log(f"[skip] {decision['scenario']}: {denied}")
            return {"action": "skip", "reason": denied}

    result = spawn_and_execute(decision, cfg, parent_thread_id, parent_depth)
    return {"action": "spawn", **result}


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def _cmd_status():
    _init_db()
    cfg = load_config()
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM spawn_log").fetchone()[0]
    today_key = f"daily_{datetime.now().strftime('%Y%m%d')}"
    hourly_key = f"hourly_{datetime.now().strftime('%Y%m%d%H')}"
    recent = conn.execute(
        "SELECT ts, scenario, agent_type, status, exit_code FROM spawn_log "
        "ORDER BY ts DESC LIMIT 8").fetchall()
    conn.close()
    print(f"=== Auto-Spawn 状态 ===")
    print(f"总开关: {'ON' if cfg.get('enabled') else 'OFF'} | 实际执行: {'ON' if cfg.get('exec_enabled') else '记录-only'}")
    print(f"配额: 今日 {_counter_get(today_key)}/{cfg.get('daily_quota')} | 本时 {_counter_get(hourly_key)}/{cfg.get('hourly_quota')} | 深度 ≤{cfg.get('max_depth')}")
    print(f"场景开关: " + ", ".join(f"{k}={'开' if v.get('enabled') else '关'}" for k, v in cfg['scenarios'].items()))
    cb = circuit_breaker_state(cfg)
    print(f"熔断: {'OPEN (' + cb.get('reason', '') + ')' if cb.get('open') else 'CLOSED'} | 连续失败 {cb.get('consecutive_failures')}/{cfg.get('circuit_breaker', {}).get('max_consecutive_failures', 3)}")
    print(f"Handoff: {'ON' if cfg.get('handoff', {}).get('enabled') else 'OFF'} → {cfg.get('handoff', {}).get('vault_dir', '')}")
    print(f"累计派生: {total} 次")
    if recent:
        print("最近记录:")
        for ts, sc, at, st, ec in recent:
            print(f"  {datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')} | {sc:<16} | {at:<14} | {st:<10} | exit={ec}")


def _cmd_config(key: str, value: str):
    cfg = load_config()
    # 支持 a.b=c 形式: scenarios.complex_task.enabled=true
    parts = key.split(".")
    cur = cfg
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    v: object = value
    if value.lower() in ("true", "false"):
        v = value.lower() == "true"
    elif value.isdigit():
        v = int(value)
    else:
        try:
            v = float(value)
        except ValueError:
            pass
    cur[parts[-1]] = v
    save_config(cfg)
    print(f"配置已更新: {key} = {v}")


def main():
    ap = argparse.ArgumentParser(description="Auto-Spawn 自动分身调度器")
    sub = ap.add_subparsers(dest="cmd")

    p_eval = sub.add_parser("evaluate", help="决策: 匹配场景→防护→派生")
    p_eval.add_argument("ctx_json", help="任务上下文 JSON")
    p_eval.add_argument("--parent-thread", default=None)
    p_eval.add_argument("--parent-depth", type=int, default=0)
    p_eval.add_argument("--force", action="store_true", help="跳过防护强制派生")

    p_spawn = sub.add_parser("spawn", help="指定场景强制派生")
    p_spawn.add_argument("ctx_json", help="{\"scenario\": \"daily_review\", \"task\": \"...\"}")

    p_exec = sub.add_parser("exec", help="(内部) 独立执行器: 运行分身任务")
    p_exec.add_argument("payload_json")

    sub.add_parser("status", help="状态/配额/场景开关/审计")
    sub.add_parser("stats", help="审计统计")
    p_cfg = sub.add_parser("config", help="配置项: auto_spawn.py config scenarios.complex_task.enabled true")
    p_cfg.add_argument("key")
    p_cfg.add_argument("value")

    args = ap.parse_args()

    if args.cmd == "evaluate":
        ctx = json.loads(args.ctx_json)
        print(json.dumps(evaluate(ctx, args.parent_thread, args.parent_depth, args.force), ensure_ascii=False, indent=2))
    elif args.cmd == "spawn":
        ctx = json.loads(args.ctx_json)
        sc = ctx.get("scenario", "daily_review")
        print(json.dumps(evaluate({**ctx, "scenario": sc}, force=True), ensure_ascii=False, indent=2))
    elif args.cmd == "exec":
        payload = json.loads(args.payload_json)
        # 合并 DEFAULT_CONFIG，确保 handoff.vault_dir 等完整配置可用
        # (否则 handoff_result 拿不到 vault_dir → 笔记误写到 cwd)
        _cfg = {**DEFAULT_CONFIG,
                **{"timeout": payload.get("timeout", 600),
                   "max_iterations": payload.get("max_iterations", 20),
                   "model": payload.get("model", "deepseek-v4-flash")}}
        _execute_worker(
            payload.get("prompt", ""), payload.get("thread_id"),
            payload.get("scenario", ""), payload.get("agent_type", ""),
            _cfg,
        )
    elif args.cmd == "status":
        _cmd_status()
    elif args.cmd == "stats":
        _init_db()
        conn = _get_conn()
        rows = conn.execute(
            "SELECT scenario, COUNT(*), SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) "
            "FROM spawn_log GROUP BY scenario").fetchall()
        conn.close()
        print("场景 | 派生次数 | 成功次数")
        for sc, n, ok in rows:
            print(f"  {sc:<18} | {n:>4} | {ok}")
    elif args.cmd == "config":
        _cmd_config(args.key, args.value)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
