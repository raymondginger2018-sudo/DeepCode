"""CompactGovernance 测试 — 压缩治理层 (接线增强) 与 CompactEngine 修复验证。

覆盖:
  1. HistoryVault 写入/去重/TF-IDF 检索
  2. retrieve_context 注入文本生成
  3. vault_after_turn 检测内置压缩摘要入库
  4. structured_summarize JSON 结构化摘要 (mock provider, 容错/降级)
  5. CompactEngine 真实格式压缩: system 保护 + 工具名提取 + 文件完整
  6. AgentSession 端到端: 压缩历史入库 + 下轮检索注入
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

# 隔离 vault 数据库 (不污染真实记忆库)
_TMP = tempfile.mkdtemp()
os.environ["DEEPCODE_VAULT_DB"] = str(Path(_TMP) / "vault.db")
os.environ["HOME"] = _TMP

from core.compact_governance import (  # noqa: E402
    HistoryVault,
    retrieve_context,
    structured_summarize,
    vault_after_turn,
)
from core.compact_engine import CompactEngine  # noqa: E402


@pytest.fixture()
def vault() -> HistoryVault:
    return HistoryVault(db_path=str(Path(_TMP) / f"v_{os.urandom(4).hex()}.db"))


def _big_tool_result() -> str:
    return json.dumps(
        {
            "ok": True,
            "name": "bash",
            "output": "\n".join(
                f"第{j}行: 股票{j}号 收盘价 {100 + j}.50 涨跌幅 +{j}.2% "
                f"成交量 {j * 10000}手 主力净流入 {j * 1000}万元"
                for j in range(1, 40)
            ),
        },
        ensure_ascii=False,
    )


def _session_file(rows: list[dict[str, Any]]) -> str:
    path = str(Path(_TMP) / f"s_{os.urandom(4).hex()}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for m in rows:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    return path


# ══════════ 1. HistoryVault ══════════

def test_vault_write_and_dedupe(vault: HistoryVault) -> None:
    n1 = vault.add("s1", "user", "主力资金净流入10.2亿元 北向加仓宁德时代")
    n2 = vault.add("s1", "user", "主力资金净流入10.2亿元 北向加仓宁德时代")  # 重复
    assert n1 == 1
    assert n2 == 0  # content_hash 去重


def test_vault_tfidf_search(vault: HistoryVault) -> None:
    vault.add("s1", "user", "主力资金今日净流入10.2亿元 北向资金加仓宁德时代")
    vault.add("s1", "user", "缠论笔划分 30分钟级别出现底背驰 反弹目标3650")
    hits = vault.search("资金流向 宁德时代", top_k=2)
    # 不相关文档 (缠论) 相似度为 0 会被过滤, 只保留相关命中
    assert hits, "应命中相关历史"
    assert hits[0]["score"] > 0
    assert "资金" in hits[0]["content"] or "宁德" in hits[0]["content"]
    # 换个查询: 缠论文档应排第一
    hits2 = vault.search("缠论 背驰", top_k=2)
    assert hits2 and "缠论" in hits2[0]["content"]


def test_vault_stats(vault: HistoryVault) -> None:
    vault.add("s1", "user", "内容甲" * 10)
    vault.add("s1", "summary", "摘要乙" * 10)
    st = vault.stats()
    assert st["total_chunks"] == 2
    assert st["by_kind"] == {"user": 1, "summary": 1}


# ══════════ 2. retrieve_context ══════════

def test_retrieve_context_injection(vault: HistoryVault) -> None:
    vault.add("s1", "user", "主力资金净流入10.2亿 北向加仓宁德时代 看多")
    text = retrieve_context(vault, "宁德时代 资金流向", top_k=1)
    assert "[历史记忆检索]" in text
    assert "宁德" in text or "资金" in text
    assert retrieve_context(vault, "完全不相关的内容xyz", top_k=1) == ""
    assert retrieve_context(None, "x") == ""


# ══════════ 3. vault_after_turn ══════════

def test_vault_after_turn_detects_compaction_summary(vault: HistoryVault) -> None:
    history = [
        {"role": "user", "content": "分析主力资金流向"},
        {"role": "assistant", "content": "正在分析"},
        {"role": "user", "content": (
            "An earlier agent worked on this task and produced the summary below "
            "of its progress and the state of the tools it used. Build on this "
            "work and avoid duplicating it. Here is the summary:\n已完成: 净流入"
        )},
    ]
    n = vault_after_turn(vault, history, "sess-1")
    assert n >= 2  # 历史 user + 摘要
    assert vault.stats()["total_chunks"] == n
    # 无压缩摘要的 history → 不入库
    assert vault_after_turn(vault, [{"role": "user", "content": "hi"}], "s2") == 0
    assert vault_after_turn(None, history, "s3") == 0


# ══════════ 4. structured_summarize ══════════

class _FakeProvider:
    def __init__(self, content: str | None = None, error: bool = False) -> None:
        self._content = content
        self._error = error

    async def chat_with_retry(self, **kwargs: Any) -> Any:
        if self._error:
            raise RuntimeError("boom")
        return type("R", (), {"content": self._content})()


def test_structured_summarize_parses_json() -> None:
    p = _FakeProvider(
        content=json.dumps(
            {"summary": "已完成", "facts": ["f1"], "decisions": ["d1"],
             "files": ["a.py"], "tasks": ["t1"]},
            ensure_ascii=False,
        )
    )
    r = structured_summarize(p, "model-x", "历史文本" * 50)
    assert r["summary"]
    assert r["facts"] == ["f1"]
    assert r["tasks"] == ["t1"]


def test_structured_summarize_tolerates_markdown_wrap() -> None:
    p = _FakeProvider(content="```json\n{\"summary\":\"s\",\"facts\":[\"a\"]}\n```")
    r = structured_summarize(p, "model-x", "x" * 600)
    assert r["summary"] == "s"


def test_structured_summarize_degrades_on_error() -> None:
    r = structured_summarize(_FakeProvider(error=True), "model-x", "x" * 600)
    assert r == {"summary": "", "facts": [], "decisions": [], "files": [], "tasks": []}


# ══════════ 5. CompactEngine 修复验证 ══════════

def _build_compact_session() -> str:
    rows: list[dict[str, Any]] = []
    rows.append({"id": "m0", "sessionId": "s1", "role": "system",
                 "content": "你是Deep Code。" * 50})
    for i in range(1, 5):
        rows.append({"id": f"u{i}", "sessionId": "s1", "role": "user",
                     "content": f"问题{i}: 分析主力资金流向"})
        rows.append({"id": f"a{i}", "sessionId": "s1", "role": "assistant",
                     "content": "开始分析",
                     "messageParams": {"tool_calls": [
                         {"id": f"c{i}", "type": "function",
                          "function": {"name": "read", "arguments": "{}"}}]}})
        rows.append({"id": f"t{i}", "sessionId": "s1", "role": "tool",
                     "content": _big_tool_result(),
                     "messageParams": {"tool_call_id": f"c{i}"}, "meta": {}})
    return _session_file(rows)


def test_compact_engine_tool_name_from_content_json() -> None:
    """真实 JSONL 工具名藏在 content JSON 里 → 白名单检查必须能提取。"""
    rows = [
        {"id": "m0", "sessionId": "s", "role": "system", "content": "sys"},
        {"id": "u1", "sessionId": "s", "role": "user", "content": "查一下"},
        {"id": "a1", "sessionId": "s", "role": "assistant", "content": "ok",
         "messageParams": {"tool_calls": [
             {"id": "c1", "type": "function",
              "function": {"name": "bash", "arguments": "{}"}}]}},
        {"id": "t1", "sessionId": "s", "role": "tool",
         "content": _big_tool_result(), "messageParams": {"tool_call_id": "c1"},
         "meta": {}},
    ]
    sf = _session_file(rows)
    eng = CompactEngine(session_file=sf, context_window=8000, keep_turns=0,
                        use_llm=False)
    tool_msg = eng._read_messages()[3]
    assert eng._get_tool_name(tool_msg) == "bash"


def test_compact_engine_runs_and_protects_system() -> None:
    sf = _build_compact_session()
    eng = CompactEngine(session_file=sf, context_window=8000, keep_turns=1,
                        use_llm=False)
    r = eng.compact("compact")
    assert r["action"] == "compact"
    assert r["tokens_saved"] > 0

    after = eng._read_messages()
    # system 消息永不被压缩
    assert all(not m.get("compacted") for m in after if m.get("role") == "system")
    # 文件完整可解析
    with open(sf, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                json.loads(line)
    # 工具结果确实被标记压缩
    assert any(m.get("compacted") for m in after if m.get("role") == "tool")


def test_compact_engine_consecutive_compaction() -> None:
    sf = _build_compact_session()
    eng = CompactEngine(session_file=sf, context_window=8000, keep_turns=0,
                        use_llm=False)
    r1 = eng.compact("compact")
    r2 = eng.compact("compact")  # 二次压缩不应崩溃
    assert r1["action"] in ("compact", "none")
    assert r2["action"] in ("compact", "none")


# ══════════ 6. AgentSession 端到端 ══════════

class _CapturingProvider:
    """记录每次 chat 收到的消息 (浅拷贝, 避免 runner 后续 append 污染), 返回空响应。"""

    def __init__(self, usage: dict[str, int] | None = None) -> None:
        self.received: list[list[dict[str, Any]]] = []
        self._usage = usage or {}

    def get_default_model(self) -> str:
        return "gpt-5.4"

    async def chat_with_retry(self, **kwargs: Any) -> Any:
        # 浅拷贝: runner 会在调用后向同一列表 append assistant 消息
        self.received.append(list(kwargs.get("messages", [])))
        return type("R", (), {
            "content": "done", "finish_reason": "stop", "usage": dict(self._usage),
            "tool_calls": [], "reasoning_content": None,
            "thinking_blocks": None, "should_execute_tools": False,
            "has_tool_calls": False,
        })()


def test_agent_session_vault_roundtrip() -> None:
    from core.agent_runtime.tools.registry import ToolRegistry
    from core.events import AgentSession, UserInput

    provider = _CapturingProvider()

    async def _run() -> None:
        s = AgentSession(provider, ToolRegistry(), model="gpt-5.4",
                         system_prompt="sys", session_key="e2e-test")
        # 第一轮: 正常对话
        [ev async for ev in s.run_stream(UserInput(text="分析宁德时代主力资金"))]
        # 手动注入压缩摘要 (模拟内置压缩结果) → 触发 turn 入库路径
        s._history.insert(
            0,
            {"role": "user",
             "content": "宁德时代 主力资金 净流入 12.5亿 北向加仓"},
        )
        s._history.insert(
            1,
            {"role": "user",
             "content": "An earlier agent worked on this task and produced the "
                        "summary below of its progress and the state of the "
                        "tools it used. Build on this work and avoid "
                        "duplicating it. Here is the summary:\n已完成主力资金分析"},
        )
        # 触发与 turn 结束后相同的入库逻辑
        from core.compact_governance import vault_after_turn
        n = vault_after_turn(s._vault, s._history, s._session_key)
        assert n >= 2
        # 第二轮: 相关问题 → provider 应收到检索注入的 system 消息
        [ev async for ev in s.run_stream(
            UserInput(text="之前分析的宁德时代资金流出了什么结论?"))]
        injected = any(
            "[历史记忆检索]" in str(m.get("content", ""))
            for msgs in provider.received
            for m in msgs
            if m.get("role") == "system"
        )
        assert injected, "第二轮应注入历史检索上下文"

    asyncio.run(_run())


# ══════════ 7. 缺口 #3: vault update/delete ══════════

def test_vault_update_rebuilds_tokens(vault: HistoryVault) -> None:
    vault.add("s1", "user", "主力资金净流入10亿")
    cid = vault.search("主力资金", top_k=1)[0]["id"]
    assert vault.update(cid, "主力资金净流入15亿 北向大幅加仓 强烈看多") is True
    hits = vault.search("大幅加仓", top_k=1)
    assert hits and "15亿" in hits[0]["content"]
    # 更新不存在的 id → False
    assert vault.update(999999, "内容") is False
    # 空内容 → False
    assert vault.update(cid, "  ") is False


def test_vault_delete_by_id_and_session(vault: HistoryVault) -> None:
    vault.add("s1", "user", "内容甲" * 10)
    cid = vault.search("内容甲", top_k=1)[0]["id"]
    assert vault.delete(chunk_id=cid) == 1
    assert vault.search("内容甲", top_k=1) == []
    vault.add("s2", "user", "内容乙" * 10)
    vault.add("s2", "user", "内容丙" * 10)
    assert vault.delete(session_key="s2") == 2
    # 无参数 → 0
    assert vault.delete() == 0


# ══════════ 8. 缺口 #5: 缓存统计 + 注入位置 ══════════

def test_vault_cache_stats(vault: HistoryVault) -> None:
    vault.record_cache(7000, 3000)
    vault.record_cache(5000, 1000)
    st = vault.cache_stats()
    assert st["hit"] == 12000
    assert st["miss"] == 4000
    assert st["hit_rate"] == 0.75
    # 空数据
    v2 = HistoryVault(db_path=str(Path(_TMP) / f"v2_{os.urandom(4).hex()}.db"))
    assert v2.cache_stats()["hit_rate"] == 0.0


def test_agent_session_injection_at_end_prefix_stable() -> None:
    """检索注入必须位于消息末尾, 保持 system+history 前缀稳定 (缓存命中前提)。"""
    from core.agent_runtime.tools.registry import ToolRegistry
    from core.events import AgentSession, UserInput

    provider = _CapturingProvider()
    history_messages: list[dict[str, Any]] = []

    async def _run() -> None:
        s = AgentSession(provider, ToolRegistry(), model="gpt-5.4",
                         system_prompt="SYS", session_key="e2e-cache")
        s._vault.add("e2e-cache", "user", "宁德时代 主力资金 净流入 12.5亿")
        [ev async for ev in s.run_stream(
            UserInput(text="宁德时代资金流出了什么结论?"))]
        msgs = provider.received[0]
        history_messages.extend(msgs)
        # 注入在末尾且为 system
        assert msgs[-1]["role"] == "system"
        assert "[历史记忆检索]" in msgs[-1]["content"]
        # 前缀稳定: 开头是 system 提示, 之后紧跟 history (user)
        assert msgs[0]["role"] == "system" and msgs[0]["content"] == "SYS"
        assert msgs[1]["role"] == "user"

    asyncio.run(_run())


def test_agent_session_records_cache_usage() -> None:
    from core.agent_runtime.tools.registry import ToolRegistry
    from core.events import AgentSession, UserInput

    provider = _CapturingProvider(
        usage={"prompt_cache_hit_tokens": 7000, "prompt_cache_miss_tokens": 3000})
    vault_db = str(Path(_TMP) / f"v3_{os.urandom(4).hex()}.db")

    async def _run() -> None:
        s = AgentSession(provider, ToolRegistry(), model="gpt-5.4",
                         system_prompt="SYS", session_key="e2e-cache2",
                         compact_vault=True)
        s._vault = HistoryVault(db_path=vault_db)  # 隔离 db
        [ev async for ev in s.run_stream(UserInput(text="分析一下"))]
        st = s._vault.cache_stats()
        assert st["hit"] == 7000
        assert st["miss"] == 3000

    asyncio.run(_run())


# ══════════ 9. 缺口 #2: 内置压缩摘要结构化渲染 ══════════

def test_render_summary_structured_json() -> None:
    from core.agent_runtime.runner import AgentRunner

    render = AgentRunner._render_summary
    text = render(
        '{"summary":"已完成主力资金分析","facts":["净流入12.5亿"],'
        '"decisions":["看多"],"files":["core/compact_engine.py"],'
        '"tasks":["验证回测"]}'
    )
    assert "[当前进度]" in text and "已完成主力资金分析" in text
    assert "[关键决策]" in text and "看多" in text
    assert "[涉及文件]" in text and "compact_engine.py" in text
    assert "[待办]" in text and "验证回测" in text


def test_render_summary_markdown_wrap_and_fallback() -> None:
    from core.agent_runtime.runner import AgentRunner

    render = AgentRunner._render_summary
    # markdown 代码块包裹
    md = render('```json\n{"summary":"s2","facts":["f1"]}\n```')
    assert "[当前进度] s2" in md
    # 散文降级 (非 JSON)
    prose = render("已完成分析，主力净流入，建议看多。")
    assert prose == "已完成分析，主力净流入，建议看多。"
    # 空
    assert render("") == ""
