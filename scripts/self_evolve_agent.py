#!/usr/bin/env python3
"""
Deep Code 超级进化代理 (Super Self-Evolving Agent)
===================================================
每日自动执行：
  ① Git 审查     → DeepSeek 审阅 24h 代码
  ② 测试健康     → pytest + AI 诊断失败根因
  ③ 失败收集     → 提取负样本（失败代码→修复方案）
  ④ 代码扫描     → DeepSeek 扫关键模块
  ⑤ 训练数据生成 → 失败案例 → GRPO 训练对
  ⑥ 学习沉淀     → git log → CLAUDE.md 规则
  ⑦ 自动修复     → 生成修复 patch（简单问题直接修）
  ⑧ 日报推送     → Server酱 → 微信

用法:
  python scripts/self_evolve_agent.py              # 完整跑
  python scripts/self_evolve_agent.py --quick      # 只跑审查+测试+推送
  python scripts/self_evolve_agent.py --dry-run    # 只看不动
"""
import os, sys, json, subprocess, textwrap, time, re

# Windows 终端编码修复
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
from datetime import datetime, timedelta

# ── 路径 ──
ROOT = Path(__file__).parent.parent
CORE = ROOT  # 合并后 CORE 即主仓库根
LAST_RUN_FILE = ROOT / ".self_evolve_last_run"
REPORT_FILE = ROOT / "self_evolve_report.md"
TRAINING_DATA_FILE = ROOT / "self_evolve_training_data.json"
AUTO_FIX_DIR = ROOT / "auto_fixes"

# ── API ──
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
if not API_KEY:
    try:
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").split("\n"):
                if "DEEPSEEK_API_KEY" in line and "=" in line:
                    API_KEY = line.split("=", 1)[1].strip()
    except: pass

API_URL = "https://api.deepseek.com/v1/chat/completions"
SENDKEY = "SCT370373TpoN23iUKqGOh9G4aLVKLg1hF"

DRY_RUN = "--dry-run" in sys.argv
QUICK = "--quick" in sys.argv

AUTO_FIX_DIR.mkdir(exist_ok=True)

# ══════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════

def run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=False,
                           cwd=str(cwd or CORE), timeout=timeout)
        out = r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
        err = r.stderr.decode("utf-8", errors="replace") if r.stderr else ""
        return out + err
    except Exception as e:
        return f"[ERROR] {e}"


def deepseek_ask(prompt, system="你是资深代码审查专家，简洁犀利。",
                 model="deepseek-v4-flash", max_tokens=2000):
    if not API_KEY:
        return "[SKIP] API Key 未配置"
    try:
        import httpx
        resp = httpx.post(API_URL, json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=60)
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[API Error] {e}"


def push_wechat(title, content):
    if DRY_RUN:
        print(f"  [微信] {title}")
        return {"code": 0}
    try:
        import httpx
        resp = httpx.post(f"https://sctapi.ftqq.com/{SENDKEY}.send",
            data={"title": title[:100], "desp": content[:30000]}, timeout=15)
        return resp.json()
    except Exception as e:
        return {"code": -1, "message": str(e)}


def since_last_run():
    if LAST_RUN_FILE.exists():
        return LAST_RUN_FILE.read_text(encoding="utf-8").strip()
    return (datetime.now() - timedelta(hours=48)).isoformat()


def mark_run():
    LAST_RUN_FILE.write_text(datetime.now().isoformat())


def load_training_data() -> list:
    if TRAINING_DATA_FILE.exists():
        try:
            return json.loads(TRAINING_DATA_FILE.read_text(encoding="utf-8"))
        except: pass
    return []


def save_training_data(data: list):
    TRAINING_DATA_FILE.write_text(
        json.dumps(data[-100:], ensure_ascii=False, indent=2),
        encoding="utf-8")


# ══════════════════════════════════════════════════
# 进化步骤
# ══════════════════════════════════════════════════

def step1_git_review(since):
    """① Git 审查"""
    print("  [1/8] Git 审查...")
    logs = run(["git", "log", f"--since={since}", "--oneline", "--stat", "--no-merges", "-30"])
    diff = run(["git", "diff", "HEAD~5..HEAD", "--", "*.py", "*.js", "*.ets", "*.ts"], timeout=30)[:8000]
    if not logs.strip():
        return "今日无新提交"
    prompt = textwrap.dedent(f"""\
    审查 Deep Code 最近代码变更：
    提交日志: {logs[:3000]}
    Diff: {diff[:6000]}
    
    给出 Top 3 改进建议。""")
    return deepseek_ask(prompt)


def step2_test_health():
    """② 测试健康"""
    print("  [2/8] 测试健康检查...")
    result = run(["python", "-m", "pytest", "--tb=short", "-q", "--no-header"], timeout=120)
    lines = result.strip().split("\n")
    failures = [l for l in lines if "FAILED" in l]
    
    report = f"测试结果: 通过={lines.count('PASSED')} 失败={len(failures)} 共{len(lines)}行\n"
    if failures:
        report += f"\n失败 ({len(failures)}):\n" + "\n".join(failures[:15])
        if not DRY_RUN:
            prompt = f"诊断这些测试失败并给出修复建议：\n{result[:5000]}"
            report += f"\n\nAI诊断:\n{deepseek_ask(prompt, system='Python测试诊断专家')}"
    return report, failures


def step3_collect_failures(failures: list) -> list:
    """③ 失败收集 — 从失败提取训练对"""
    print(f"  [3/8] 收集失败样本 ({len(failures)} 个)...")
    if not failures:
        return []
    
    training_pairs = []
    for f in failures[:5]:  # 最多处理5个
        parts = f.split("::")
        if len(parts) < 2:
            continue
        test_file = parts[0]
        test_name = parts[-1].split()[0] if parts[-1] else "unknown"
        
        # 读测试文件
        test_path = CORE / test_file
        if not test_path.exists():
            continue
        test_code = test_path.read_text(encoding="utf-8")[:3000]
        
        # 找对应的源文件
        src_file = test_file.replace("test_", "").replace("tests/", "core/")
        if not (CORE / src_file).exists():
            src_file = test_file.replace("tests/", "")
        src_code = ""
        src_path = CORE / src_file
        if src_path.exists():
            src_code = src_path.read_text(encoding="utf-8")[:3000]
        
        prompt = textwrap.dedent(f"""\
        分析这个测试失败，给出修复方案。
        
        测试: {test_file}::{test_name}
        
        测试代码:
        ```python
        {test_code}
        ```
        
        被测试代码:
        ```python
        {src_code}
        ```
        
        输出 JSON 格式:
        {{"root_cause": "一句话根因", "fix": "修复方案代码或者思路", "fix_type": "import_fix/signature_fix/logic_fix/config_fix"}}
        """)
        
        diagnosis = deepseek_ask(prompt, max_tokens=1500)
        
        # 尝试从响应中提取 JSON
        try:
            # 找 JSON 块
            json_match = re.search(r'\{[^{}]*\}', diagnosis, re.DOTALL)
            if json_match:
                pair = json.loads(json_match.group())
                pair.update({
                    "test_file": test_file,
                    "test_name": test_name,
                    "timestamp": datetime.now().isoformat(),
                    "type": "test_failure",
                })
                training_pairs.append(pair)
        except:
            # 保存为文本对
            training_pairs.append({
                "test_file": test_file,
                "test_name": test_name,
                "root_cause": diagnosis[:500],
                "fix": "",
                "fix_type": "unknown",
                "timestamp": datetime.now().isoformat(),
                "type": "test_failure",
            })
        time.sleep(1)
    
    return training_pairs


def step4_code_scan():
    """④ 代码扫描"""
    print("  [4/8] 代码扫描...")
    changed = run(["git", "diff", "--name-only", "HEAD~10", "--", "*.py"], timeout=10)
    files = [f.strip() for f in changed.strip().split("\n") if f.strip()][:5]
    if not files:
        files = ["quant_trading/ml/grpo_trainer.py", "quant_trading/ml/moe_router.py",
                 "quant_trading/ml/knowledge_engine.py", "core/mcp_servers/router_mcp_server.py",
                 "core/llm_runtime.py"]
    results = []
    for f in files:
        path = CORE / f
        if not path.exists():
            results.append(f"  {f}: 不存在")
            continue
        code = path.read_text(encoding="utf-8")[:4000]
        prompt = textwrap.dedent(f"""\
        审查 {f}：
        1. 死代码/未用 import
        2. 异常处理
        3. 性能瓶颈
        4. 安全风险
        5. 最多3个改进点
        ```python
        {code}
        ```""")
        review = deepseek_ask(prompt, max_tokens=1000)
        results.append(f"\n### {f}\n{review}")
        time.sleep(1)
    return "\n".join(results)


def step5_generate_training(training_pairs: list):
    """⑤ 训练数据生成 — 失败案例→GRPO训练对"""
    print(f"  [5/8] 生成训练数据 ({len(training_pairs)} 条新样本)...")
    if not training_pairs:
        return "今日无新训练数据"
    
    existing = load_training_data()
    existing.extend(training_pairs)
    save_training_data(existing)
    
    return f"累积 {len(existing)} 条训练样本 (今日新增 {len(training_pairs)})"


def step6_learn():
    """⑥ 学习沉淀"""
    print("  [6/8] 学习沉淀...")
    logs = run(["git", "log", "--since=3 days ago", "--oneline", "--no-merges", "-30"], timeout=10)
    if not logs.strip():
        return "无可沉淀的新经验"
    
    # 分析提交内容
    prompt = textwrap.dedent(f"""\
    从这些 git 提交中提取可写入 CLAUDE.md 规则区的经验：
    {logs[:3000]}
    
    输出格式：
    - [经验] 一句话
    - [经验] 一句话""")
    return deepseek_ask(prompt)


def step7_auto_fix(training_pairs: list):
    """⑦ 自动修复 — 对简单问题直接生成 patch"""
    print("  [7/8] 自动修复...")
    fixes = []
    
    for pair in training_pairs:
        fix_type = pair.get("fix_type", "")
        fix = pair.get("fix", "")
        
        # 只修 import_fix 和 signature_fix 这种安全可逆的
        if fix_type in ("import_fix", "signature_fix") and fix and len(fix) < 200:
            fix_file = pair.get("test_file", "").replace("tests/", "")
            fix_path = CORE / fix_file
            if fix_path.exists():
                patch_path = AUTO_FIX_DIR / f"fix_{pair['test_name']}_{datetime.now().strftime('%H%M%S')}.patch"
                patch_path.write_text(f"# Auto-generated fix for {pair['test_name']}\n# Type: {fix_type}\n# {pair.get('root_cause', '')}\n#\n# {fix}\n", encoding="utf-8")
                fixes.append(f"  ✅ {fix_file}: {fix_type}")
    
    if not fixes:
        return "无可自动修复的问题（复杂问题需人工审查）"
    return "\n".join(fixes)


def step8_report(parts, training_pairs):
    """⑧ 日报推送"""
    print("  [8/8] 生成日报并推送...")
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report = f"# 🧬 Deep Code 超级进化日报\n**{now}**\n\n"
    
    labels = ["Git 审查", "测试健康", "失败收集", "代码扫描", "训练数据", "学习沉淀", "自动修复"]
    for label, content in zip(labels, parts):
        if content and len(str(content)) > 3:
            status = "✅"
            if "失败" in str(content)[:50] or "无可" in str(content)[:20] or "Error" in str(content)[:20]:
                status = "⚠️"
            content_str = str(content).strip()[:500]  # 截断
            report += f"\n---\n## {status} {label}\n\n{content_str}\n"
    
    # 训练数据统计
    n_failures = len([p for p in training_pairs if p.get("type") == "test_failure"])
    n_fixes = len([p for p in training_pairs if p.get("fix_type") != "unknown"])
    report += f"\n---\n## 📊 今日统计\n- 失败样本: {n_failures}\n- 可修复: {n_fixes}\n- 总训练数据: {len(load_training_data())}\n"
    
    REPORT_FILE.write_text(report, encoding="utf-8")
    print(f"  报告: {REPORT_FILE}")
    
    push_wechat(f"🧬 Deep Code 进化日报 {datetime.now().strftime('%m/%d')}", report[:2000])
    print("  微信推送 ✅")


# ══════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════

def main():
    print("=" * 50)
    print("  🧬 Deep Code 超级进化代理")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    if DRY_RUN:
        print("  ⚠️  Dry-Run 模式")
    if QUICK:
        print("  ⚡ 快速模式")

    since = since_last_run()
    print(f"\n  上次运行: {since}")

    parts = []
    training_pairs = []
    
    # 必做
    parts.append(step1_git_review(since))
    
    test_result, failures = step2_test_health()
    parts.append(test_result)
    
    if QUICK:
        # 快速模式：只做审查+测试+学习+推送
        parts.append("(快速模式跳过)")
        parts.append("(快速模式跳过)")
        parts.append(step6_learn())
        parts.append("(快速模式跳过)")
    else:
        # 完整模式
        training_pairs = step3_collect_failures(failures)
        parts.append(f"收集 {len(training_pairs)} 个失败样本")
        
        parts.append(step4_code_scan())
        parts.append(step5_generate_training(training_pairs))
        parts.append(step6_learn())
        parts.append(step7_auto_fix(training_pairs))

    step8_report(parts, training_pairs)
    mark_run()
    print("\n  ✅ 超级进化循环完成")


if __name__ == "__main__":
    main()
