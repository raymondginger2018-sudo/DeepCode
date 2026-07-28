#!/usr/bin/env python3
"""
Effort Router — Claude Code 风格推理深度控制
═══════════════════════════════════════════════
根据 effort level 自动选择模型 + Token 预算 + 推理参数。

Effort Level 映射:
  low    → 快速模型, 低 Token 预算 (简单任务)
  medium → 标准模型, 默认预算   (日常任务)
  high   → 深度模型, 高预算     (复杂分析)
  xhigh  → 最强模型, 最大预算   (架构设计)
  max    → 全模型集成           (关键决策, 6模型投票)

用法:
  from effort_router import route_effort
  model, budget, params = route_effort("high")
"""

import json
from pathlib import Path
from typing import Dict, Tuple, Optional

SETTINGS_PATH = Path(__file__).parent.parent / "settings.json"

DEFAULT_EFFORT_CONFIG = {
    "default_level": "medium",
    "levels": {
        "low": {
            "model": "deepseek-v4-flash",
            "max_tokens": 2048,
            "temperature": 0.3,
            "description": "简单问答、CLI 命令、文件操作",
        },
        "medium": {
            "model": "deepseek-v4-flash",
            "max_tokens": 4096,
            "temperature": 0.6,
            "description": "日常编程、代码审查、文档生成",
        },
        "high": {
            "model": "deepseek-v4-pro",
            "max_tokens": 8192,
            "temperature": 0.5,
            "description": "复杂分析、架构设计、量化策略",
        },
        "xhigh": {
            "model": "deepseek-r1",
            "max_tokens": 16384,
            "temperature": 0.3,
            "description": "深度推理、数学证明、算法优化",
        },
        "max": {
            "model": "ensemble",
            "max_tokens": 32768,
            "temperature": 0.4,
            "description": "6模型集成投票 (关键决策)",
            "ensemble_models": [
                "deepseek-v4-pro",
                "glm-5.2",
                "qwen3.5-122b",
                "minimax-m3",
                "mistral-large",
                "stockmark-100b",
            ],
        },
    },
}


def load_effort_config() -> dict:
    """加载 effort 配置，缺失时使用默认值"""
    if SETTINGS_PATH.exists():
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        cfg = raw.get("effort", {})
        merged = DEFAULT_EFFORT_CONFIG.copy()
        if "default_level" in cfg:
            merged["default_level"] = cfg["default_level"]
        if "levels" in cfg:
            for lv, lv_cfg in cfg["levels"].items():
                if lv in merged["levels"]:
                    merged["levels"][lv].update(lv_cfg)
                else:
                    merged["levels"][lv] = lv_cfg
        return merged
    return DEFAULT_EFFORT_CONFIG


def route_effort(level: Optional[str] = None) -> Tuple[str, int, Dict]:
    """
    路由 effort level → (model, max_tokens, params)

    Args:
        level: low/medium/high/xhigh/max, None = default

    Returns:
        (model_name, max_tokens, extra_params)
    """
    cfg = load_effort_config()
    lv = level or cfg["default_level"]

    if lv not in cfg["levels"]:
        print(f"[effort_router] Unknown level '{lv}', falling back to medium")
        lv = "medium"

    lv_cfg = cfg["levels"][lv]
    model = lv_cfg["model"]
    max_tokens = lv_cfg["max_tokens"]
    params = {
        "temperature": lv_cfg.get("temperature", 0.6),
        "effort": lv,
    }

    if model == "ensemble" and "ensemble_models" in lv_cfg:
        params["ensemble_models"] = lv_cfg["ensemble_models"]

    return model, max_tokens, params


def list_levels() -> str:
    """列出所有 effort level 及对应配置"""
    cfg = load_effort_config()
    lines = [
        f"{'Level':<8} {'Model':<22} {'Tokens':<8} Description",
        "-" * 70,
    ]
    for lv, lv_cfg in cfg["levels"].items():
        marker = " *" if lv == cfg["default_level"] else "  "
        lines.append(
            f"{marker}{lv:<6} {lv_cfg['model']:<22} {lv_cfg['max_tokens']:<8} "
            f"{lv_cfg['description']}"
        )
    return "\n".join(lines)


# ── CLI 入口 ──
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        level = sys.argv[1]
        model, budget, params = route_effort(level)
        print(json.dumps({
            "level": level,
            "model": model,
            "max_tokens": budget,
            "params": params,
        }, indent=2))
    else:
        print(list_levels())
