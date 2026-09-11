# DEEPCODE 磁盘管理策略 (Starlark)
# ==================================
# 用于 find_by_size / find_by_date 的后续决策。
# 用法: starlark.starlark_rules(rules_code=此文件, context={...})

def _check_size_limit(size_kb, limit_kb):
    """检查文件大小是否超过限制"""
    return int(size_kb) > int(limit_kb)

def _check_age_days(mtime_iso, max_age_days):
    """检查文件是否超过最大保留天数"""
    # 简化: 假设 mtime_iso 已经是 days_ago
    return int(mtime_iso) > int(max_age_days)

# ── 规则集 ──

def decide(path, size_kb, days_old, suffix):
    """
    输入: 文件路径, 大小(KB), 距今修改天数, 后缀
    输出: "keep" | "delete" | "archive" | "warn"
    """
    # 规则 1: 日志文件 > 100MB 且超过 7 天 → 删除
    if suffix in (".log", ".tmp") and _check_size_limit(size_kb, 102400) and _check_age_days(days_old, 7):
        return "delete"

    # 规则 2: 任何 > 500MB 的文件 → 警告
    if _check_size_limit(size_kb, 512000):
        return "warn"

    # 规则 3: node_modules / .git 内的文件 → 永远不动
    if "/node_modules/" in path or "/.git/" in path or path.startswith("node_modules") or path.startswith(".git"):
        return "keep"

    # 规则 4: __pycache__ 内文件 > 30 天 → 删除
    if "__pycache__" in path and _check_age_days(days_old, 30):
        return "delete"

    # 规则 5: .bak / .orig 文件 > 30 天 → 归档
    if suffix in (".bak", ".orig", ".old") and _check_age_days(days_old, 30):
        return "archive"

    # 默认: 保留
    return "keep"


# ── 统计函数 ──

def summarize(decisions):
    """输入: [{"path":..., "action":...}]，输出: 统计摘要"""
    counts = {"keep": 0, "delete": 0, "archive": 0, "warn": 0}
    total_freed = 0
    for d in decisions:
        counts[d.get("action", "keep")] += 1
        if d.get("action") == "delete":
            total_freed += int(d.get("size_kb", 0))
    return {
        "total_files": len(decisions),
        "counts": counts,
        "estimated_freed_mb": int(total_freed / 1024),
    }
