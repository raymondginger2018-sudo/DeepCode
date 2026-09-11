#!/usr/bin/env python3
"""PREFIX-CACHE 记账统计 — 查看 DeepSeek 上下文缓存命中/未命中/请求数。

数据来源:
  1. HistoryVault (CLI 主通道记账): ~/.deepcode/compact_vault.db 的 meta 表
     （由 core/compact_governance.py HistoryVault.record_cache 写入，
       键: cache_hit_total / cache_miss_total / cache_requests）
  2. token-saver 各缓存库: F:/DEEPCODE/data/*.db 的 accounting 表
     （op='cache_hit', saved_tokens = 缓存节省的输入 token）

用法:
    python F:/DEEPCODE/scripts/cache_stats.py           # 只查 HistoryVault
    python F:/DEEPCODE/scripts/cache_stats.py --all     # 同时汇总 token-saver 各库

注意: 若显示"暂无记录"，说明修复后尚未产生新的 LLM 请求 ——
先随便跑一个会话/查询，再执行本命令即可看到数字。
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# DeepSeek 前缀缓存命中价 (CNY / 1M tokens) — 与 router_mcp_server.py L47-50 一致
CACHE_PRICE = {
    "deepseek-v4-flash": 0.02,   # 输入 ¥1 / 输出 ¥2 / 命中 ¥0.02 (1:50:100)
    "deepseek-v4-pro":   0.025,  # 输入 ¥3 / 输出 ¥6 / 命中 ¥0.025 (1:120:240)
}


def vault_db_path() -> Path:
    """HistoryVault 数据库路径 (兼容 core/compact_governance.py 的 env 覆盖)。"""
    env = os.environ.get("DEEPCODE_VAULT_DB")
    if env:
        return Path(env)
    return Path.home() / ".deepcode" / "compact_vault.db"


def query_meta(db: Path) -> dict[str, int]:
    """读取 HistoryVault meta 表中 cache_% 键值。"""
    out: dict[str, int] = {}
    if not db.exists():
        return out
    try:
        conn = sqlite3.connect(db, timeout=10)
        try:
            rows = conn.execute(
                "SELECT key, value FROM meta WHERE key LIKE 'cache_%'"
            ).fetchall()
            for k, v in rows:
                out[k] = int(v or 0)
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return out


def query_token_saver(db: Path) -> tuple[int, int, int] | None:
    """读取 token-saver accounting 表, 返回 (input, output, saved)。
    库结构为 accounting(id, ts, op, input_tokens, output_tokens, saved_tokens, mode, detail)。
    无该表/无记录时返回 None。
    """
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(db, timeout=10)
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            if "accounting" not in tables:
                return None
            rows = conn.execute(
                "SELECT COALESCE(SUM(input_tokens),0),"
                "       COALESCE(SUM(output_tokens),0),"
                "       COALESCE(SUM(saved_tokens),0),"
                "       COUNT(*) "
                "FROM accounting WHERE op='cache_hit'"
            ).fetchone()
            if not rows or rows[3] == 0:
                return None
            return int(rows[0]), int(rows[1]), int(rows[2])
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def fmt_row(key: str, value: int) -> str:
    return f"  {key:<22} = {value:,}"


def summarize_vault(db: Path, label: str) -> tuple[int, int, int]:
    """打印 HistoryVault 统计，返回 (hit, miss, requests)。"""
    print(f"=== {label} ===")
    print(f"  DB: {db}  |  存在: {db.exists()}")
    meta = query_meta(db)
    if not meta:
        print("  (暂无 cache_% 记录 —— 修复后尚未产生新请求，先跑一个会话再查)")
        return 0, 0, 0

    for k, v in sorted(meta.items()):
        print(fmt_row(k, v))

    hit = meta.get("cache_hit_total", 0)
    miss = meta.get("cache_miss_total", 0)
    req = meta.get("cache_requests", 0)
    total = hit + miss
    rate = hit / total * 100 if total else 0.0

    print(
        f"  => 命中率 {rate:.1f}%  "
        f"(hit={hit:,} miss={miss:,} 请求={req:,})"
    )
    print()
    return hit, miss, req


def summarize_token_saver(db: Path, label: str) -> tuple[int, int, int]:
    """打印 token-saver accounting 统计，返回 (saved, 0, requests)。"""
    print(f"=== {label} ===")
    print(f"  DB: {db}  |  存在: {db.exists()}")
    agg = query_token_saver(db)
    if agg is None:
        print("  (accounting 表无 cache_hit 记录)")
        print()
        return 0, 0, 0

    inp, out, saved = agg
    print(f"  input_tokens     = {inp:,}")
    print(f"  output_tokens    = {out:,}")
    print(f"  saved_tokens     = {saved:,}  (缓存省下的输入 token)")
    print(f"  => 应用层缓存共节省 {saved:,} tokens")
    print()
    # token-saver 的 saved_tokens 即缓存节省量, 计入 hit 侧
    return saved, 0, 0


def main() -> int:
    ap = argparse.ArgumentParser(description="PREFIX-CACHE 记账统计")
    ap.add_argument(
        "--all",
        action="store_true",
        help="同时汇总 token-saver 各缓存库 (cache/mcp) 的命中记录",
    )
    args = ap.parse_args()

    print("PREFIX-CACHE 记账统计\n")
    hit, miss, req = summarize_vault(vault_db_path(), "HistoryVault (CLI 主通道记账)")

    if args.all:
        base = Path("F:/DEEPCODE/data")
        for name in ("token_saver_cache.db", "token_saver_mcp.db"):
            db = base / name
            if db.exists():
                h, m, r = summarize_token_saver(db, f"token-saver ({name})")
                hit += h
                miss += m
                req += r
            else:
                print(f"=== token-saver ({name}) ===\n  DB: {db}  不存在\n")

        print("=== 汇总 (仅含存在的库) ===")
        total = hit + miss
        rate = hit / total * 100 if total else 0.0
        print(f"  hit(含应用层节省)={hit:,}  miss={miss:,}  请求={req:,}  命中率={rate:.1f}%")
        if hit:
            for model, price in CACHE_PRICE.items():
                print(f"  约省 ¥{hit * price / 1_000_000:.4f} (按 {model} 命中价)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
