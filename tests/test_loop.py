"""Tests for the P3 loop engine — real git + real pytest backpressure.

The LoopTask orchestration is exercised with a scripted "agent" (no model)
that writes buggy code on round 0 and correct code on round 1; the test
runner is the REAL run_tests (real pytest), so the loop's success is driven
by actually running tests — not by anything the fake agent claims. This is
the rigorous-verification stance the loop itself embodies.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop.backpressure import TestResult, run_tests  # noqa: E402
from core.loop.guards import LoopGuards  # noqa: E402
from core.loop.policy import decide  # noqa: E402
from core.loop.state import (  # noqa: E402
    STATUS_EXHAUSTED,
    STATUS_STALLED,
    STATUS_SUCCEEDED,
    LoopState,
    RoundRecord,
)
from core.loop.task import LoopTask  # noqa: E402

_HAVE_PYTEST = shutil.which("pytest") or True  # invoked via python -m


# -- LoopState ---------------------------------------------------------------


def test_state_save_load_roundtrip(tmp_path):
    st = LoopState(
        goal="g", workspace=str(tmp_path), test_command="pytest", max_rounds=5
    )
    st.add_round(RoundRecord(index=0, tests_passed=False, test_signature="abc"))
    st.save()
    loaded = LoopState.load(tmp_path)
    assert loaded is not None
    assert loaded.goal == "g"
    assert loaded.round_count == 1
    assert loaded.rounds[0].test_signature == "abc"
    assert not loaded.is_terminal


def test_state_load_absent(tmp_path):
    assert LoopState.load(tmp_path) is None


# -- backpressure (real subprocess) ------------------------------------------


def _write_project(ws: Path, impl: str) -> None:
    (ws / "calc.py").write_text(impl)
    (ws / "test_calc.py").write_text(
        textwrap.dedent(
            """
            from calc import add
            def test_add():
                assert add(2, 3) == 5
            """
        )
    )


def test_run_tests_pass_and_fail(tmp_path):
    _write_project(tmp_path, "def add(a, b):\n    return a + b\n")
    ok = run_tests(str(tmp_path), "python -m pytest -q")
    assert ok.ran and ok.passed and ok.returncode == 0 and ok.signature == ""

    # Different LENGTH from the correct impl so the .pyc cache is invalidated
    # by size even if the rewrite lands in the same mtime second (a fast-test
    # artifact; real loop rounds are seconds apart).
    _write_project(tmp_path, "def add(a, b):\n    return 0\n")
    bad = run_tests(str(tmp_path), "python -m pytest -q")
    assert bad.ran and not bad.passed and bad.signature
    # Signature is stable across repeated identical failures (stall detection).
    bad2 = run_tests(str(tmp_path), "python -m pytest -q")
    assert bad.signature == bad2.signature


def test_run_tests_no_command_is_neutral(tmp_path):
    r = run_tests(str(tmp_path), "")
    assert not r.ran and not r.passed


# -- policy ------------------------------------------------------------------


def test_policy_success_on_green():
    st = LoopState(goal="g", workspace="/w", max_rounds=5)
    st.add_round(RoundRecord(index=0, tests_passed=True))
    d = decide(st)
    assert d.stop and d.status == STATUS_SUCCEEDED


def test_policy_exhausted_at_cap():
    st = LoopState(goal="g", workspace="/w", max_rounds=2)
    st.add_round(RoundRecord(index=0, tests_passed=False, test_signature="a"))
    st.add_round(RoundRecord(index=1, tests_passed=False, test_signature="b"))
    d = decide(st)
    assert d.stop and d.status == STATUS_EXHAUSTED


def test_policy_stalled_on_repeated_signature():
    st = LoopState(goal="g", workspace="/w", max_rounds=10)
    for i in range(3):
        st.add_round(RoundRecord(index=i, tests_passed=False, test_signature="same"))
    d = decide(st, stall_rounds=3)
    assert d.stop and d.status == STATUS_STALLED


def test_policy_continues_while_progressing():
    st = LoopState(goal="g", workspace="/w", max_rounds=10)
    st.add_round(RoundRecord(index=0, tests_passed=False, test_signature="a"))
    st.add_round(RoundRecord(index=1, tests_passed=False, test_signature="b"))
    assert not decide(st).stop


# -- LoopTask (orchestration, real test backpressure) ------------------------


def _scripted_runner(scripts):
    """Return a round runner that applies scripts[i] to the workspace."""
    calls = {"n": 0}

    async def _run(workspace: str, prompt: str):
        i = calls["n"]
        calls["n"] += 1
        script = scripts[min(i, len(scripts) - 1)]
        script(Path(workspace), prompt)
        return "completed", f"round {i} done"

    return _run, calls


@pytest.mark.skipif(not _HAVE_PYTEST, reason="pytest required")
def test_loop_succeeds_when_agent_fixes_on_round_two(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()

    def round0(w: Path, prompt: str):  # buggy (different length → no stale .pyc)
        _write_project(w, "def add(a, b):\n    return 0\n")

    def round1(w: Path, prompt: str):  # fixed — only reached because round0 failed
        assert "FAILING" in prompt or "failed" in prompt.lower()
        _write_project(w, "def add(a, b):\n    return a + b\n")

    runner, calls = _scripted_runner([round0, round1])
    task = LoopTask(
        goal="make calc.add work",
        workspace=str(ws),
        test_command="python -m pytest -q",
        max_rounds=5,
        round_runner=runner,
    )
    result = asyncio.run(task.run())

    assert result.succeeded
    assert result.state.status == STATUS_SUCCEEDED
    assert result.state.round_count == 2  # failed once, fixed, stopped
    assert result.state.rounds[0].tests_passed is False
    assert result.state.rounds[1].tests_passed is True
    # State was persisted to disk.
    assert LoopState.load(ws).status == STATUS_SUCCEEDED


@pytest.mark.skipif(not _HAVE_PYTEST, reason="pytest required")
def test_loop_exhausts_when_never_fixed(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()

    def always_buggy(w: Path, prompt: str):
        # A DIFFERENT wrong answer each round, so it's "exhausted", not "stalled".
        n = len(list(w.glob("*.marker")))
        (w / f"{n}.marker").write_text("x")
        _write_project(w, f"def add(a, b):\n    return a - b - {n}\n")

    runner, _ = _scripted_runner([always_buggy])
    task = LoopTask(
        goal="g",
        workspace=str(ws),
        test_command="python -m pytest -q",
        max_rounds=3,
        round_runner=runner,
    )
    result = asyncio.run(task.run())
    assert not result.succeeded
    assert result.state.status == STATUS_EXHAUSTED
    assert result.state.round_count == 3


def test_loop_failure_ratchet_escalates_prompt(tmp_path):
    """When the same failure repeats, the next round's prompt escalates to
    force a different approach (the failure ratchet)."""
    ws = tmp_path / "proj"
    ws.mkdir()
    prompts: list[str] = []

    def buggy_same_way(w: Path, prompt: str):
        prompts.append(prompt)
        _write_project(w, "def add(a, b):\n    return 0\n")  # always the same failure

    runner, _ = _scripted_runner([buggy_same_way])
    task = LoopTask(
        goal="g",
        workspace=str(ws),
        test_command="python -m pytest -q",
        max_rounds=4,
        round_runner=runner,
    )
    asyncio.run(task.run())
    # Round 0 has no ratchet; a later round (same failure repeated) escalates.
    assert not any("persisted through" in p for p in prompts[:1])
    assert any("persisted through" in p for p in prompts[1:])


def test_loop_round_error_is_captured(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()

    async def boom(workspace: str, prompt: str):
        raise RuntimeError("agent exploded")

    task = LoopTask(goal="g", workspace=str(ws), round_runner=boom, max_rounds=3)
    result = asyncio.run(task.run())
    assert result.state.status == "error"
    assert "exploded" in result.state.stop_reason


def test_loop_emits_round_events(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    _write_project(ws, "def add(a, b):\n    return a + b\n")
    seen = []

    def hook(state, record, test):
        seen.append((record.index, test.passed))

    async def noop(workspace: str, prompt: str):
        return "completed", "ok"

    task = LoopTask(
        goal="g",
        workspace=str(ws),
        test_command="python -m pytest -q",
        round_runner=noop,
        on_event=hook,
        max_rounds=3,
    )
    asyncio.run(task.run())
    assert seen and seen[0][0] == 0


# -- P2: StormBreaker 短路 → STATUS_STALLED ------------------------------------


def test_loop_storm_breaker_short_circuits_to_stalled(tmp_path):
    """StormBreaker 熔断后，LoopTask 短路到 stalled 而非烧更多测试预算。"""
    ws = tmp_path / "proj"
    ws.mkdir()
    _write_project(ws, "def add(a, b):\n    return a + b\n")

    guards = LoopGuards()
    tc = type("TC", (), {"name": "bash", "arguments": {"command": "x"}})()
    for _ in range(4):  # 4 次相同失败签名 → count > max_failures(3) → 熔断
        guards.observe_batch(
            [tc],
            ["Error: boom"],
            [{"name": "bash", "status": "error"}],
        )
    assert guards.storm.blocked

    async def noop(workspace: str, prompt: str):
        return "completed", "ok"

    task = LoopTask(
        goal="g",
        workspace=str(ws),
        round_runner=noop,
        test_runner=lambda w, c: TestResult(
            ran=True,
            passed=False,
            returncode=1,
            summary="boom",
            output_tail="boom",
            signature="boom",
        ),
        guards=guards,
        max_rounds=3,
    )
    result = asyncio.run(task.run())
    assert result.state.status == STATUS_STALLED
    assert "storm breaker" in (result.state.stop_reason or "")
    assert result.state.round_count == 1


# -- P4: LoopTask 默认装配 / 复用 guards ---------------------------------------


def test_loop_task_default_assembles_guards(tmp_path):
    """未传 guards 时 LoopTask 默认装配一个 LoopGuards 实例。"""
    ws = tmp_path / "proj"
    ws.mkdir()
    task = LoopTask(goal="g", workspace=str(ws), max_rounds=1)
    assert isinstance(task._guards, LoopGuards)


def test_loop_task_reuses_passed_guards(tmp_path):
    """传入 guards 时 LoopTask 复用同一实例（跨轮状态持久的前提）。"""
    ws = tmp_path / "proj"
    ws.mkdir()
    guards = LoopGuards()
    task = LoopTask(goal="g", workspace=str(ws), guards=guards, max_rounds=1)
    assert task._guards is guards


def test_default_round_runner_forwards_guards(tmp_path, monkeypatch):
    """默认 round runner 把同一 guards 实例传给每轮的 build_agent_session。"""
    ws = tmp_path / "proj"
    ws.mkdir()
    guards = LoopGuards()
    captured: dict = {}

    class _FakeTaskComplete:
        type = "task_complete"
        final_text = "ok"
        stop_reason = "completed"

    class _FakeMsg:
        msg = _FakeTaskComplete()

    class _FakeSession:
        async def run_stream(self, message):
            yield _FakeMsg()

    def fake_build_agent_session(**kwargs):
        captured.update(kwargs)
        return _FakeSession(), "fake-model", None

    monkeypatch.setattr("core.loop.task.build_agent_session", fake_build_agent_session)
    task = LoopTask(
        goal="g",
        workspace=str(ws),
        guards=guards,
        max_rounds=1,
        test_runner=lambda w, c: TestResult(
            ran=True,
            passed=True,
            returncode=0,
            summary="ok",
            output_tail="",
            signature="",
        ),
    )
    result = asyncio.run(task.run())
    assert captured.get("guards") is guards
    assert result.state.status == STATUS_SUCCEEDED
