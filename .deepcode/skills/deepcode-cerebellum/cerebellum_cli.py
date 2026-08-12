#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode 小脑 — Hooks 入口 CLI
═══════════════════════════════
供 settings.json hooks 调用:
  SessionStart → session_start (设置快照 + vault 索引)
  PostTask     → post_task --task "..." (经验提炼)
  SessionEnd   → session_end (会话摘要沉淀, 强制触发)
  OnError      → on_error (失败信号 + 隐式负反馈 + 自动进化提案)
  PreCompact   → pre_compact (会话摘要, 压缩时触发; 已迁移至 SessionEnd)

用法:
  python cerebellum_cli.py session_start
  python cerebellum_cli.py post_task --task "修复了 XXX bug"
  python cerebellum_cli.py session_end --session-id abc123
  python cerebellum_cli.py on_error   # hooks 传入 stdin JSON, 也可 --error/--tool

记忆进化引擎 (对标 MindMemOS):
  dreaming / dreaming_history     — 离线记忆巩固
  feedback_add / feedback_list    — 反馈闭环 (评分回灌检索排序)
  skill_signal / skill_signals    — Skill 执行信号采集
  skill_propose / skill_proposals — 进化提案 (失败>=3 时 LLM 分析)
  skill_apply / skill_reject      — 人工确认后应用/拒绝
  benchmark / benchmark_history   — 自评测 (Recall@1/@k + MRR)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cerebellum_core import (
    CerebellumMemory,
    consolidated_search,
    experience_record,
    index_vault,
    session_briefing,
    session_summarize,
    settings_snapshot,
)
# 记忆进化引擎 (Dreaming / 反馈闭环 / Skill 进化信号 / 评测)
# evolution 依赖 core, cli → evolution → core 无循环依赖, 可顶层导入
from cerebellum_evolution import (
    benchmark_list,
    benchmark_run,
    dreaming_history,
    dreaming_run,
    feedback_add,
    feedback_list,
    skill_evolution_apply,
    skill_evolution_list,
    skill_evolution_propose,
    skill_evolution_reject,
    skill_signal_add,
    skill_signals_list,
    learning_loop_detect,
)

EXIT_OK = 0
EXIT_ERROR = 1


def _log(msg: str) -> None:
    print(f"[cerebellum] {msg}", file=sys.stderr, flush=True)


def cmd_session_start(task_hint: str = "") -> int:
    """SessionStart: 设置快照 + 索引 vault + 生成前车之鉴简报 (小脑预热)"""
    ok = True
    try:
        r = settings_snapshot("all")
        _log(f"设置快照: {r['ok']} ({len(r['snapshots'])} 份)")
    except Exception as e:
        _log(f"设置快照失败: {e}")
        ok = False
    try:
        r = index_vault()
        _log(f"知识库索引: {r.get('ok', False)} ({r.get('indexed_notes', 0)} 篇)")
    except Exception as e:
        _log(f"vault 索引失败: {e}")
        ok = False
    try:
        r = session_briefing(task_hint)
        n = r.get("count", 0)
        brief = r.get("briefing", "")[:60]
        _log(f"前车之鉴简报: {n} 条经验 | {brief}...")
    except Exception as e:
        _log(f"简报生成失败: {e}")
        ok = False
    return EXIT_OK if ok else EXIT_ERROR


def cmd_post_task(task: str) -> int:
    """PostTask: 用本地模型提炼经验 + 学习循环周期巡检 (24h 节流)"""
    if not task:
        _log("跳过: 无任务描述")
        return EXIT_OK
    ok = True
    try:
        r = experience_record(task)
        _log(f"经验已沉淀: {r['lesson'][:80]}")
    except Exception as e:
        _log(f"经验提炼失败: {e}")
        ok = False

    # 学习循环周期巡检: 每天最多跑一次全量检测 (纯规则, 不依赖 LLM)
    throttle = Path(__file__).parent / "data" / "learning_loop_throttle.txt"
    try:
        last = 0
        if throttle.is_file():
            last = float(throttle.read_text(encoding="utf-8").strip() or 0)
        if time.time() - last < 24 * 3600:
            _log("学习循环巡检跳过 (24h 节流)")
        else:
            ll = learning_loop_detect(persist=True, limit=20)
            n = ll.get("count", 0)
            throttle.write_text(str(time.time()), encoding="utf-8")
            _log(f"学习循环巡检完成: {n} 条候选")
    except Exception as e:
        _log(f"学习循环巡检失败: {e}")
        ok = False
    return EXIT_OK if ok else EXIT_ERROR


# 最近一次会话上下文获取失败原因 (供 cmd_pre_compact 传入 session_summarize 记录)
_last_context_error = ""
# hook stdin payload 中提取的 session_id (PreCompact/SessionEnd hook 触发时注入)
_stdin_session_id = ""


def _read_session_context(session_id: str, max_chars: int = 4000) -> str:
    """PreCompact: 自动读取当前会话 jsonl 提取 user/assistant/tool 对话内容。

    查找顺序:
      1. stdin 非空时优先用 stdin 传入的原始对话文本 (hook 可管道传入)
         - 若 stdin 是 hooks payload JSON (含 hook_event_name) → 提取 session_id,
           继续走文件查找, 避免把 payload 元数据误当对话文本
      2. ~/.deepcode/projects/<project>/ 下与 session_id 同名的 .jsonl (子串匹配)
      3. 显式 session_id 未匹配到文件 → 回退 adhoc: 取 mtime 最新的 .jsonl
    提取增强 (修复 ②):
      - 除 user/assistant 外, tool 消息(工具结果)也纳入上下文 (截断 200 字符)
      - 若仍无内容, 兜底提取 system 消息中长度 20-2000 的摘要块, 标记 [system]
    返回截断到 max_chars 的对话文本; 找不到时返回 "" 并记录原因到 _last_context_error。
    """
    global _last_context_error, _stdin_session_id
    _last_context_error = ""
    _stdin_session_id = ""

    # 1) 显式 stdin 优先 (hook 可把原始对话通过管道传入)
    try:
        if not sys.stdin.isatty():
            data = sys.stdin.read()
            if data and data.strip():
                # hooks payload (PreCompact/SessionEnd) 是 JSON 元数据而非对话文本:
                # 提取 session_id 后继续走 jsonl 文件查找
                try:
                    obj = json.loads(data)
                    if isinstance(obj, dict) and obj.get("hook_event_name"):
                        sid = str(obj.get("session_id") or "").strip()
                        if sid:
                            _stdin_session_id = sid
                            if session_id == "adhoc":
                                session_id = sid
                    else:
                        return data.strip()[:max_chars]
                except Exception:
                    return data.strip()[:max_chars]
    except Exception:
        pass

    projects = Path.home() / ".deepcode" / "projects"
    if not projects.is_dir():
        _last_context_error = f"会话目录不存在: {projects}"
        return ""

    # 收集全部候选 jsonl (排除索引/非会话文件)
    all_files = []
    for proj in sorted(projects.iterdir()):
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            if f.name != "sessions-index.json":
                all_files.append(f)

    cand = None
    if session_id != "adhoc":
        matched = [f for f in all_files if session_id in f.name]
        if matched:
            cand = matched[0] if len(matched) == 1 else max(matched, key=lambda f: f.stat().st_mtime)
        else:
            # 修复 ①: 显式 session_id 匹配失败 → 回退 adhoc (mtime 最新)
            _last_context_error = f"session_id '{session_id}' 未匹配到会话文件, 已回退最新会话"
    if cand is None:
        if all_files:
            cand = max(all_files, key=lambda f: f.stat().st_mtime)
        else:
            _last_context_error = f"未找到任何会话 jsonl: {projects}"
            return ""

    # 修复 ④: stdin 未注入 session_id 时, 用实际定位到的会话文件反填 (mtime 最新),
    # 使 cmd_pre_compact 的覆盖逻辑能关联到真实会话而非 adhoc
    if not _stdin_session_id and cand is not None:
        _stdin_session_id = cand.stem

    try:
        lines = []
        with open(cand, "r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                except Exception:
                    continue
                role = obj.get("role", "")
                content = obj.get("content", "")
                if not isinstance(content, str) or not content.strip():
                    continue
                # 修复 ②: user/assistant/tool 都纳入; tool 结果截断防占满上下文
                if role in ("user", "assistant", "tool"):
                    text = content.strip()
                    if role == "tool" and len(text) > 200:
                        text = text[:200] + "\n...[工具结果截断]..."
                    elif len(text) > 600:
                        # 超长单条消息, 保留头尾
                        text = text[:400] + "\n...[中略]...\n" + text[-150:]
                    lines.append(f"[{role}] {text}")
                if sum(len(l) for l in lines) >= max_chars:
                    break
        joined = "\n".join(lines)
        if joined.strip():
            return joined[:max_chars]

        # 兜底: 无 user/assistant/tool 消息 → 提取 system 摘要块 (compacted 内容)
        fallback = []
        with open(cand, "r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                except Exception:
                    continue
                content = obj.get("content", "")
                if isinstance(content, str) and 20 <= len(content.strip()) <= 2000:
                    fallback.append(f"[system] {content.strip()[:200]}")
                if len(fallback) >= 20:
                    break
        if fallback:
            _last_context_error = "无 user/assistant/tool 消息, 已提取 system 摘要块兜底"
            return "\n".join(fallback)[:max_chars]

        _last_context_error = f"会话文件 {cand.name} 无任何可提取内容"
        return ""
    except Exception as e:
        _last_context_error = f"读取会话文件失败: {e}"
        _log(f"读取会话文件失败: {e}")
        return ""


def cmd_pre_compact(session_id: str = "adhoc") -> int:
    """PreCompact: 自动读取最近会话上下文, 本地模型生成真实摘要"""
    try:
        context = _read_session_context(session_id)
        # hook 触发时 (PreCompact/SessionEnd), stdin payload 注入了真实 session_id,
        # 用它覆盖默认的 adhoc, 使摘要落库时关联到正确的会话
        if _stdin_session_id:
            session_id = _stdin_session_id
        # 修复 ③: 上下文为空时, 把 _read_session_context 记录的失败原因传给 session_summarize,
        # 让规则摘要占位符带上原因, 便于区分"真失败"与"正常空会话"
        empty_reason = _last_context_error if not context.strip() else ""
        # upsert_window_s=300: Stop/SessionEnd 每轮触发时, 5 分钟内同会话摘要 UPDATE 覆盖,
        # 防止表膨胀刷屏; PreCompact 与 SessionEnd 共享此入口, 去重逻辑一处生效
        r = session_summarize(context, session_id, empty_reason=empty_reason,
                              upsert_window_s=300)
        # token 记账 (方案1 口径): before=上下文 token, after=摘要 token, saved=差值
        acct = (r.get("token_accounting") or {}) if isinstance(r, dict) else {}
        saved_t = int(acct.get("saved_tokens", 0))
        _log(f"会话摘要已持久化 (session={session_id}, 上下文 {len(context)} 字符, "
             f"saved ~{saved_t} tokens)")
        # memento 式 checkpoint 回注: 持久化成功后, 把摘要作为
        # hookSpecificOutput.additionalContext 输出到 stdout (UTF-8),
        # 由 engine.run_pre_compact → runner._maybe_compact 回注到压缩历史。
        summary_text = (r.get("summary") or "").strip() if isinstance(r, dict) else ""
        if summary_text:
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreCompact",
                    "additionalContext": summary_text,
                }
            }, ensure_ascii=False), flush=True)
        return EXIT_OK
    except Exception as e:
        _log(f"会话摘要失败: {e}")
        return EXIT_ERROR


def cmd_on_error(error: str = "", tool: str = "") -> int:
    """OnError hook: 解析 hook stdin JSON, 采集失败信号 + 隐式负反馈。

    Hook 输入 (Claude Code 风格 stdin JSON):
      {"session_id": "...", "tool_name": "Bash", "tool_input": {...}, "tool_response": {...}}
    流程:
      1. 失败信号 → skill_signals (signal_type=failure)
      2. 隐式负反馈 → feedback_entries (target_type=tool, rating=-1, source=on_error)
      3. 信号 >= 3 时自动触发 skill_evolution_propose (LLM 分析失败模式)
    """
    data: dict = {}
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                data = json.loads(raw)
    except Exception:
        pass
    if not data:
        data = {"tool_name": tool, "error": error}

    tool_name = str(data.get("tool_name") or tool or "unknown")[:80]
    resp = data.get("tool_response") or {}
    if isinstance(resp, dict):
        err_text = str(data.get("error") or error or resp.get("error") or "")[:200]
        exit_code = resp.get("exit_code")
        if not err_text and exit_code is not None:
            err_text = f"exit_code={exit_code}"
    else:
        err_text = str(data.get("error") or error or resp)[:200]
    context = str(data.get("tool_input") or "")[:200]

    # 1) 失败信号
    try:
        r = skill_signal_add(tool_name, signal_type="failure",
                             context=context, error=err_text)
        count = r.get("failure_count", r.get("count", 0))
        _log(f"失败信号已记录: {tool_name} (累计 {count} 次失败)")
    except Exception as e:
        _log(f"失败信号记录失败: {e}")

    # 2) 隐式负反馈 (rating=-1 合法)
    try:
        fb = feedback_add("tool", tool_name, -1, source="on_error",
                          comment=err_text[:100])
        _log(f"隐式负反馈已记录: {tool_name} ({fb.get('rating')})")
    except Exception as e:
        _log(f"隐式负反馈失败: {e}")

    # 3) 达到阈值自动生成进化提案 (计数 < 3 时零 LLM 开销)
    try:
        prop = skill_evolution_propose(tool_name)
        if prop.get("proposed"):
            _log(f"⚠ Skill 进化提案已生成: #{prop.get('proposal_id')} ({tool_name})")
        elif prop.get("pending"):
            _log(f"已有待处理提案 #{prop.get('proposal_id')}, 跳过")
    except Exception as e:
        _log(f"进化提案跳过: {e}")
    return EXIT_OK


def cmd_dreaming(kind: str, use_llm: bool) -> int:
    """Dreaming: 离线记忆巩固 (跨会话聚类 + LLM 合并 + 归档)"""
    try:
        r = dreaming_run(kind=kind, use_llm=use_llm)
        _log(f"Dreaming 完成: 扫描 {r.get('items_scanned', 0)} 条, "
             f"{r.get('clusters', 0)} 簇, 合并 {r.get('merged', 0)} 条")
        return EXIT_OK
    except Exception as e:
        _log(f"Dreaming 失败: {e}")
        return EXIT_ERROR


def cmd_dreaming_history(limit: int) -> int:
    """查看 Dreaming 运行历史"""
    try:
        r = dreaming_history(limit=limit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"读取 Dreaming 历史失败: {e}")
        return EXIT_ERROR


def cmd_consolidated_search(query: str, kind: str, limit: int,
                            as_of: str, strict: bool) -> int:
    """检索已巩固的精华记忆/知识 (Dreaming 产物, 可按 kind 过滤 + as_of 时态)"""
    try:
        r = consolidated_search(query, kind=kind or None, limit=limit,
                                as_of=as_of or None, strict=strict)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"检索巩固记忆失败: {e}")
        return EXIT_ERROR


def cmd_feedback_add(target_type: str, target_key: str, rating: int,
                     source: str, comment: str) -> int:
    """显式反馈: 用户/大脑对检索结果的评分回灌"""
    try:
        r = feedback_add(target_type, target_key, rating,
                         source=source, comment=comment)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"反馈写入失败: {e}")
        return EXIT_ERROR


def cmd_feedback_list(target_type: str, target_key: str, limit: int) -> int:
    """查看反馈记录"""
    try:
        r = feedback_list(target_type=target_type or None,
                          target_key=target_key or None, limit=limit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"读取反馈失败: {e}")
        return EXIT_ERROR


def cmd_skill_signal(skill_name: str, signal_type: str,
                     context: str, error: str) -> int:
    """手动记录 Skill 执行信号 (failure/success)"""
    try:
        r = skill_signal_add(skill_name, signal_type=signal_type,
                             context=context, error=error)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"信号记录失败: {e}")
        return EXIT_ERROR


def cmd_skill_signals(skill_name: str, limit: int) -> int:
    """查看 Skill 信号"""
    try:
        r = skill_signals_list(skill_name=skill_name or None, limit=limit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"读取信号失败: {e}")
        return EXIT_ERROR


def cmd_skill_propose(skill_name: str, use_llm: bool) -> int:
    """触发 Skill 进化提案 (信号 >= 3 时 LLM 分析失败模式)"""
    try:
        r = skill_evolution_propose(skill_name, use_llm=use_llm)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"提案生成失败: {e}")
        return EXIT_ERROR


def cmd_skill_proposals(status: str) -> int:
    """列出进化提案 (pending/applied/rejected)"""
    try:
        r = skill_evolution_list(status=status or None)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"读取提案失败: {e}")
        return EXIT_ERROR


def cmd_skill_apply(proposal_id: int) -> int:
    """人工确认后应用提案 — 向 SKILL.md 追加进化记录章节"""
    try:
        r = skill_evolution_apply(proposal_id)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"应用提案失败: {e}")
        return EXIT_ERROR


def cmd_skill_reject(proposal_id: int) -> int:
    """拒绝提案"""
    try:
        r = skill_evolution_reject(proposal_id)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"拒绝提案失败: {e}")
        return EXIT_ERROR


def cmd_benchmark(top_k: int) -> int:
    """自评测记忆检索 — Recall@1/@k + MRR, 输出 Markdown 报告"""
    try:
        r = benchmark_run(top_k=top_k)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"评测失败: {e}")
        return EXIT_ERROR


def cmd_benchmark_history(limit: int) -> int:
    """查看评测历史"""
    try:
        r = benchmark_list(limit=limit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return EXIT_OK
    except Exception as e:
        _log(f"读取评测历史失败: {e}")
        return EXIT_ERROR


def main() -> int:
    ap = argparse.ArgumentParser(prog="cerebellum_cli", description="DeepCode 小脑 hooks 入口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ss = sub.add_parser("session_start", help="SessionStart: 设置快照 + vault 索引 + 简报")
    p_ss.add_argument("--task-hint", default="", help="本次会话任务提示(用于语义检索经验)")
    p_pt = sub.add_parser("post_task", help="PostTask: 经验提炼")
    p_pt.add_argument("--task", default="")
    p_pc = sub.add_parser("pre_compact", help="PreCompact: 会话摘要")
    p_pc.add_argument("--session-id", default="adhoc")
    p_se = sub.add_parser("session_end", help="SessionEnd: 会话摘要沉淀 (强制, 不依赖 compact)")
    p_se.add_argument("--session-id", default="adhoc")

    # ── 记忆进化引擎 (对标 MindMemOS: Dreaming / 反馈闭环 / Skill 进化信号 / 评测) ──
    p_oe = sub.add_parser("on_error", help="OnError hook: 失败信号 + 隐式负反馈 + 自动提案")
    p_oe.add_argument("--error", default="")
    p_oe.add_argument("--tool", default="")

    p_dr = sub.add_parser("dreaming", help="Dreaming: 离线记忆巩固")
    p_dr.add_argument("--kind", choices=["session", "experience", "knowledge"], default="session")
    p_dr.add_argument("--no-llm", action="store_true", help="跳过 LLM 合并, 用规则合并")
    p_dh = sub.add_parser("dreaming_history", help="Dreaming 运行历史")
    p_dh.add_argument("--limit", type=int, default=5)

    p_cs = sub.add_parser("consolidated_search", help="检索已巩固的精华记忆/知识 (Dreaming 产物)")
    p_cs.add_argument("query")
    p_cs.add_argument("--kind", choices=["session", "experience", "knowledge"], default=None)
    p_cs.add_argument("--limit", type=int, default=5)
    p_cs.add_argument("--as-of", default="", help="时态截止 (ISO 时间), 只返回该时刻前已存在的巩固记忆")
    p_cs.add_argument("--strict", action="store_true",
                      help="严格模式: 存在被时态过滤的未来记忆时抛 ClockDomainError")

    p_fa = sub.add_parser("feedback_add", help="显式反馈: 评分回灌检索排序")
    p_fa.add_argument("--target-type", required=True,
                      choices=["memory", "experience", "session", "note", "tool"])
    p_fa.add_argument("--target-key", required=True)
    p_fa.add_argument("--rating", required=True, type=int, choices=[1, -1, -2])
    p_fa.add_argument("--source", default="explicit",
                      choices=["explicit", "implicit", "on_error"])
    p_fa.add_argument("--comment", default="")
    p_fl = sub.add_parser("feedback_list", help="查看反馈记录")
    p_fl.add_argument("--target-type", default="")
    p_fl.add_argument("--target-key", default="")
    p_fl.add_argument("--limit", type=int, default=20)

    p_ssig = sub.add_parser("skill_signal", help="手动记录 Skill 执行信号")
    p_ssig.add_argument("--skill-name", required=True)
    p_ssig.add_argument("--signal-type", choices=["failure", "success"], default="failure")
    p_ssig.add_argument("--context", default="")
    p_ssig.add_argument("--error", default="")
    p_slist = sub.add_parser("skill_signals", help="查看 Skill 信号")
    p_slist.add_argument("--skill-name", default="")
    p_slist.add_argument("--limit", type=int, default=20)

    p_sp = sub.add_parser("skill_propose", help="触发 Skill 进化提案 (信号 >= 3 时 LLM 分析)")
    p_sp.add_argument("--skill-name", required=True)
    p_sp.add_argument("--no-llm", action="store_true")
    p_spro = sub.add_parser("skill_proposals", help="列出进化提案")
    p_spro.add_argument("--status", choices=["pending", "applied", "rejected"], default="")
    p_sa = sub.add_parser("skill_apply", help="确认并应用提案 → SKILL.md 追加进化记录")
    p_sa.add_argument("--proposal-id", required=True, type=int)
    p_sr = sub.add_parser("skill_reject", help="拒绝提案")
    p_sr.add_argument("--proposal-id", required=True, type=int)

    p_bm = sub.add_parser("benchmark", help="自评测记忆检索 (Recall@1/@k + MRR)")
    p_bm.add_argument("--top-k", type=int, default=5)
    p_bh = sub.add_parser("benchmark_history", help="查看评测历史")
    p_bh.add_argument("--limit", type=int, default=5)

    args = ap.parse_args()
    if args.cmd == "session_start":
        return cmd_session_start(getattr(args, "task_hint", ""))
    if args.cmd == "post_task":
        return cmd_post_task(args.task)
    if args.cmd == "pre_compact":
        return cmd_pre_compact(args.session_id)
    if args.cmd == "session_end":
        return cmd_pre_compact(args.session_id)
    # 记忆进化引擎子命令
    if args.cmd == "on_error":
        return cmd_on_error(args.error, args.tool)
    if args.cmd == "dreaming":
        return cmd_dreaming(args.kind, not args.no_llm)
    if args.cmd == "dreaming_history":
        return cmd_dreaming_history(args.limit)
    if args.cmd == "consolidated_search":
        return cmd_consolidated_search(args.query, args.kind, args.limit,
                                       args.as_of, args.strict)
    if args.cmd == "feedback_add":
        return cmd_feedback_add(args.target_type, args.target_key, args.rating,
                                args.source, args.comment)
    if args.cmd == "feedback_list":
        return cmd_feedback_list(args.target_type, args.target_key, args.limit)
    if args.cmd == "skill_signal":
        return cmd_skill_signal(args.skill_name, args.signal_type,
                                args.context, args.error)
    if args.cmd == "skill_signals":
        return cmd_skill_signals(args.skill_name, args.limit)
    if args.cmd == "skill_propose":
        return cmd_skill_propose(args.skill_name, not args.no_llm)
    if args.cmd == "skill_proposals":
        return cmd_skill_proposals(args.status)
    if args.cmd == "skill_apply":
        return cmd_skill_apply(args.proposal_id)
    if args.cmd == "skill_reject":
        return cmd_skill_reject(args.proposal_id)
    if args.cmd == "benchmark":
        return cmd_benchmark(args.top_k)
    if args.cmd == "benchmark_history":
        return cmd_benchmark_history(args.limit)
    ap.print_help()
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
