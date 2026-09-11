# -*- coding: utf-8 -*-
"""
█▀█ █░█ █▀█ █▄▄ █▀█ █▀▀
█▀▄ █▄█ █▀▀ █▄█ █▄█ ██▄

Router MCP · 通用智能路由引擎（纯付费 · 已移除所有免费模型）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  轻量问答   → V4 Flash   (¥1)   ⚡
  复杂分析   → V4 Pro     (¥3)   🧠
  缠论推演   → V4 Pro     (¥3)   📐
  日常数据   → 本地计算   (¥0)   💻

注意：所有免费外部 API 模型已于 2026-08-21 移除（用户要求）。
分层路由中 grunt/code/deep 层不再有免费通道，直接走付费兜底。
local 层保留（本机 Ollama，隐私任务数据不出网）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os
import re
import json
import math
import asyncio
import traceback
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
from mcp.server.fastmcp import FastMCP

# 本地小脑级联 + 语义缓存（自包含模块，缺失/出错时自动降级为纯路由模式）
try:
    import router_cascade as cascade
except Exception:  # noqa: BLE001 — 任何异常都不影响主服务
    cascade = None

mcp = FastMCP("router-mcp")

# ════════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════════

DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
API_KEY = os.environ.get("DEEPSEEK_API_KEY")


def _load_env_key(name: str) -> str:
    """解析 API Key：环境变量优先 → 向上逐级搜索 .env（兼容 core 与 .dsh 两种部署路径）。

    与 router_mcp_gateway._load_api_key 同一套约定：MCP 服务器进程由 Harness
    spawn，只注入 DEEPSEEK_API_KEY；其余 Provider Key 由本函数自 .env 补齐。
    .env 可能是 GBK/ANSI 编码（Windows 中文环境），需编码回退。
    """
    key = os.environ.get(name, "").strip()
    if key:
        return key
    # 向上逐级找 .env：core 部署在 F:/DEEPCODE/core/mcp_servers（3 级上），
    # .dsh 部署在 F:/DEEPCODE/.dsh/mcp-servers/router（4 级上），都收敛到 F:/DEEPCODE/.env
    candidates = []
    p = Path(__file__).resolve().parent
    for _ in range(6):
        p = p.parent
        candidates.append(p / ".env")
    seen = set()
    for env_file in candidates:
        if env_file in seen or not env_file.exists():
            continue
        seen.add(env_file)
        try:
            raw = env_file.read_bytes()
            text = None
            for enc in ("utf-8", "gbk", "latin-1"):
                try:
                    text = raw.decode(enc)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if text is None:
                text = raw.decode("utf-8", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == name:
                        key = v.strip().strip('"').strip("'")
                        if key:
                            return key
        except OSError:
            continue
    return ""

# DEEPSEEK_API_KEY 兜底：环境变量缺失时自 .env 读取（DEEPCODE 等客户端
# 若未注入该变量，服务器也能从项目 .env 自举，避免 import 期崩溃）
if not API_KEY:
    API_KEY = _load_env_key("DEEPSEEK_API_KEY")
if not API_KEY:
    raise RuntimeError("DEEPSEEK_API_KEY 未设置。请创建 .env 文件并添加 DEEPSEEK_API_KEY=your_key")

# ── Provider API Key（仅保留付费 Key；免费模型已全部移除）──
ZHIPU_API_KEY = _load_env_key("ZHIPU_API_KEY")
SILICONFLOW_API_KEY = _load_env_key("SILICONFLOW_API_KEY")
# SCNET（国家超算互联网）· GLM-5.2 托管端点 — Token Plan 密钥 sk-tp- 开头
SCNET_API_KEY = _load_env_key("SCNET_TP_API_KEY") or _load_env_key("SCNET_API_KEY")

# Provider API Base
ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
SILICONFLOW_BASE = "https://api.siliconflow.cn/v1"
# SCNET OpenAI 兼容端点（另提供 Anthropic 协议: https://api.scnet.cn/api/llm/anthropic）
SCNET_BASE = "https://api.scnet.cn/api/llm/v1"

# 本地 Ollama server（隐私/NDA 兜底；Ollama OpenAI 兼容端点 11434 常驻，无需手动起 llama-server）
LOCAL_LLAMA_BASE = "http://127.0.0.1:11434/v1"

# 成本表（¥/百万 tokens）— 与 deepseek-direct 合并后统一口径
# cache_hit 为 DeepSeek 前缀缓存命中价（逆向恢复官方 CNY 牌价，v4-flash 1:50:100、v4-pro 1:120:240）
MODEL_COSTS = {
    "deepseek-v4-flash": {"in": 1,  "out": 2,  "cache_hit": 0.02,  "emoji": "⚡", "label": "V4 Flash", "note": "官方牌价 ¥1/¥2，缓存命中 ¥0.02"},
    "deepseek-v4-pro":   {"in": 3,  "out": 6,  "cache_hit": 0.025, "emoji": "🧠", "label": "V4 Pro",   "note": "官方牌价 ¥3/¥6，缓存命中 ¥0.025"},
    "deepseek-r1":       {"in": 3,  "out": 6,  "cache_hit": 0.025, "emoji": "🔴", "label": "R1→V4 Pro", "note": "R1 已下线，深度推理/极限推理自动落到 V4 Pro（成本按 V4 Pro 计）"},
    "glm-5.2":           {"in": 1.4, "out": 4.4, "cache_hit": 1.4, "emoji": "🌐", "label": "GLM-5.2 (SCNET)", "note": "SCNET Token Plan 约 ¥1.4/¥4.4（估），1M ctx 推理型"},
    "glm-5.3":           {"in": 1.4, "out": 4.4, "cache_hit": 0.26, "emoji": "🧿", "label": "GLM-5.3 (Z.ai)", "note": "智谱最新旗舰，Intelligence Index 60，Agent 全球第二，同 GLM-5.2 定价"},
    "kimi-k3":           {"in": 3.9, "out": 19.5, "cache_hit": 3.9, "emoji": "🌙", "label": "Kimi-K3 (SCNET)", "note": "月之暗面旗舰，Intelligence Index 60，Token Plan 最贵积分（输出 171,429）"},
    "qwen3.8-max":       {"in": 2.0, "out": 6.0, "cache_hit": 2.0, "emoji": "☁️", "label": "Qwen3.8-Max (SCNET)", "note": "通义旗舰，2.4T 参数 MoE，1M ctx，思考/快速双模式"},
}

# 模型 ID 映射（API 实际使用的模型名）
# 注意：DeepSeek 已迁移 v4 系列，官方仅支持 deepseek-v4-pro / deepseek-v4-flash；
#       deepseek-reasoner 别名已废弃（实际返回 v4-flash），故 deepseek-r1 映射到 v4-pro。
MODEL_ID_MAP = {
    "deepseek-v4-pro":   "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek-r1":       "deepseek-v4-pro",
    "glm-5.2":           "GLM-5.2",   # 内部 key 小写，SCNET API 实际 ID 大写
    "glm-5.3":           "glm-5.3",   # 智谱官方，小写
    "kimi-k3":           "Kimi-K3",   # SCNET，大写
    "qwen3.8-max":       "Qwen3.8-Max", # SCNET，大小写混合
}

# ── effort→模型 统一映射（单一事实来源，合并自 deepcode-telemetry/provider_router.py）──
# effort 表达「用户期望的思考深度」；classify() 表达「内容复杂度」。
# 优先级: force_model > effort > force_route/classify 自动分类
EFFORT_MODEL_MAP = {
    "low":    "deepseek-v4-flash",
    "medium": "deepseek-v4-flash",
    "high":   "deepseek-v4-pro",
    "xhigh":  "deepseek-r1",
    "max":    "deepseek-r1",
}
EFFORT_MAX_TOKENS = {
    "low": 2048, "medium": 4096, "high": 8192, "xhigh": 16384, "max": 32768,
}
EFFORT_LABELS = {
    "low": "⚡ 快速（Flash）", "medium": "⚡ 标准（Flash）", "high": "🧠 深度（Pro）",
    "xhigh": "🔴 推理（R1）", "max": "🔴 极限推理（R1）",
}

# ════════════════════════════════════════════════════════════════
# 🪜 分层策略（纯付费 · 已移除所有免费外部 API 模型）
# ════════════════════════════════════════════════════════════════
# 所有免费外部模型已于 2026-08-21 移除（用户要求「没鸟用」）。
# 各层仅保留付费兜底通道，不再有免费重试链。
#   grunt 打杂   → V4 Flash (¥1)  直接付费
#   code  代码活 → V4 Flash (¥1)  直接付费
#   main  主力   → 硅基 V4-Flash → V4 Flash (¥1)  双付费通道
#   deep  攻坚   → V4 Pro   (¥3)  直接付费
#   local 本地   → Qwen2.5-3B（本机 Ollama，隐私/NDA，数据不出网）
TIER_POLICY = {
    "grunt": {
        "label": "打杂（翻译/格式化/抽取/摘要/整理）",
        "emoji": "🧹",
        "channels": [
            {"provider": "deepseek", "model": "deepseek-v4-flash", "cost": 1, "timeout": 90,
             "note": "付费兜底 ¥1/¥2"},
        ],
    },
    "code": {
        "label": "代码活（代码审查/PoC 起草/模式匹配）",
        "emoji": "🔧",
        "channels": [
            {"provider": "deepseek", "model": "deepseek-v4-flash", "cost": 1, "timeout": 90,
             "note": "付费兜底 ¥1/¥2"},
        ],
    },
    "main": {
        "label": "主力（复杂分析/多轮迭代/长文档）",
        "emoji": "⚡",
        "channels": [
            {"provider": "siliconflow-dsv4flash", "model": "deepseek-ai/DeepSeek-V4-Flash", "cost": 1,
             "timeout": 90,
             "note": "硅基流动 V4-Flash · 同模型国内直连 · 余额不足(30001)自动降级官方"},
            {"provider": "deepseek", "model": "deepseek-v4-flash", "cost": 1, "timeout": 90,
             "note": "V4 Flash · ¥1/¥2 · 快且稳"},
            ],
    },
    "deep": {
        "label": "攻坚（利用链推演/深度推理/报告评审）",
        "emoji": "🔴",
        "channels": [
            {"provider": "deepseek", "model": "deepseek-v4-pro", "cost": 3, "timeout": 120,
             "note": "付费兜底 ¥3/¥6"},
        ],
    },
    "local": {
        "label": "本地隐私（NDA/敏感任务，数据不出网）",
        "emoji": "🖥️",
        "channels": [
            {"provider": "local", "model": "qwen2.5:3b", "cost": 0, "timeout": 600,
             "note": "Ollama Qwen2.5-3B · 本机11434常驻 · 快且隐私（数据不出网）"},
        ],
    },
}

# 实验性通道：已移除（所有免费外部 API 模型已于 2026-08-21 清除）

TIER_LABELS = {t: f"{v['emoji']} {v['label']}" for t, v in TIER_POLICY.items()}

# 分层运行统计
_tier_stats = {
    "total": 0,
    "cost_yuan": 0.0,
    "free_calls": 0,
    "paid_calls": 0,
    "fallbacks": 0,
    "per_tier": {},
    "per_channel": {},   # f"{tier}::{provider}::{model}" → {"ok": n, "fail": n}
    "last": None,
}


def effort_to_model(effort: str = "high") -> dict:
    """effort → (模型, max_tokens) 推荐。非法 effort 回退 high。"""
    effort = (effort or "high").lower().strip()
    if effort not in EFFORT_MODEL_MAP:
        effort = "high"
    model = EFFORT_MODEL_MAP[effort]
    costs = MODEL_COSTS[model]
    return {
        "effort": effort,
        "model": model,
        "model_id": MODEL_ID_MAP.get(model, model),
        "max_tokens": EFFORT_MAX_TOKENS[effort],
        "label": costs["label"],
        "emoji": costs["emoji"],
        "cost_in": costs["in"],
        "cost_out": costs["out"],
        "note": EFFORT_LABELS[effort],
    }


# ════════════════════════════════════════════════════════════════
# 🏭 Provider 抽象层（合并自 deepcode-telemetry/provider_router.py）
# ════════════════════════════════════════════════════════════════
import urllib.request  # noqa: E402 — 标准库，与 httpx 并存用于 Provider 层
import ssl  # noqa: E402
from dataclasses import dataclass, field as dc_field  # noqa: E402
from enum import Enum  # noqa: E402

# Windows 上 Python 自带证书库经常过期（曾致外部 Provider CA 链校验失败）。
# 优先使用 certifi 的 CA bundle，缺失时回退系统默认。
try:
    import certifi  # noqa: E402
    _PROVIDER_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # noqa: BLE001 — certifi 缺失/损坏时回退默认
    _PROVIDER_SSL_CTX = None


class ProviderType(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"


class AuthType(str, Enum):
    API_KEY = "api_key"
    BEARER = "bearer"


@dataclass
class ProviderConfig:
    name: str
    type: ProviderType
    api_base: str
    api_key: str = ""
    model: str = "deepseek-chat"
    max_tokens: int = 8192
    temperature: float = 0.6
    timeout: int = 60
    headers: dict = dc_field(default_factory=dict)
    # 扩展请求体字段（GLM-5.2 等推理型模型: thinking/reasoning_effort/extra_body）
    extra_payload: dict = dc_field(default_factory=dict)


@dataclass
class ProviderResponse:
    ok: bool
    content: str = ""
    model: str = ""
    usage: dict = dc_field(default_factory=dict)
    error: str = ""
    latency_ms: int = 0
    retry_after: int = 0  # 429 限流窗口秒数（0=无）


class BaseProvider:
    """Provider 基类 — chat() 返回 ProviderResponse，不抛异常。"""

    def __init__(self, config: ProviderConfig):
        self.config = config

    def chat(self, messages: list, model: str = "", max_tokens: int = 0,
             temperature: float = 0.0) -> ProviderResponse:
        raise NotImplementedError

    def health_check(self) -> bool:
        return bool(self.config.api_key)


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI 兼容协议（DeepSeek / Zhipu / SiliconFlow / NVIDIA NIM / OpenRouter / 本地 vLLM 等）"""

    def chat(self, messages: list, model: str = "", max_tokens: int = 0,
             temperature: float = 0.0) -> ProviderResponse:
        cfg = self.config
        model = model or cfg.model
        max_tokens = max_tokens or cfg.max_tokens
        temp = temperature if temperature > 0 else cfg.temperature
        url = cfg.api_base.rstrip("/") + "/chat/completions"
        # Kimi-K3 是"永远思考"模型（moonshot 直连协议）：只接受
        # max_completion_tokens + 顶层 reasoning_effort，不接受
        # temperature/top_p/top_k/repetition_penalty（实测这些字段导致 400/断连）
        is_kimi = "kimi" in cfg.name.lower() or "kimi" in str(model).lower()
        payload = {"model": model, "messages": messages}
        if is_kimi:
            payload["max_completion_tokens"] = max_tokens
            payload["reasoning_effort"] = "high"
        else:
            payload["max_tokens"] = max_tokens
            payload["temperature"] = temp
            payload["top_p"] = 0.8
            # Qwen3-Coder 官方推荐采样参数: top_k=20, repetition_penalty=1.05
            # 仅附加到支持这些字段的 OpenAI 兼容平台（硅基/智谱/NIM 等）；
            # DeepSeek 官方 API 不支持 top_k/repetition_penalty，传了会 400，故跳过
            if cfg.name != "deepseek":
                payload["top_k"] = 20
                payload["repetition_penalty"] = 1.05
        # GLM-5.2 等推理型模型扩展参数（thinking 开关 + reasoning_effort 思考强度，
        # SCNET/智谱 OpenAI 兼容端点支持；deepseek 官方不支持，跳过）
        if cfg.extra_payload:
            if is_kimi:
                # K3 只接受顶层 reasoning_effort（thinking 可选），其余扩展字段忽略
                if "reasoning_effort" in cfg.extra_payload:
                    payload["reasoning_effort"] = cfg.extra_payload["reasoning_effort"]
            else:
                payload.update(cfg.extra_payload)
        headers = {"Content-Type": "application/json", **cfg.headers}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        t0 = datetime.now()
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
            )
            if _PROVIDER_SSL_CTX is not None:
                with urllib.request.urlopen(req, timeout=cfg.timeout,
                                            context=_PROVIDER_SSL_CTX) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            else:
                with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            usage = data.get("usage", {})
            msg = (data.get("choices") or [{}])[0].get("message", {})
            # 推理型模型（gpt-oss / deepseek think 等）把回复放 reasoning_content/reasoning
            content = (msg.get("content") or msg.get("reasoning_content")
                       or msg.get("reasoning") or "")
            return ProviderResponse(
                ok=True, content=content, model=model, usage=usage,
                latency_ms=int((datetime.now() - t0).total_seconds() * 1000),
            )
        except urllib.error.HTTPError as e:  # 4xx/5xx — 单独捕获以读取 Retry-After
            retry_after = 0
            ra = e.headers.get("Retry-After") if e.headers else None
            if ra and ra.isdigit():
                retry_after = int(ra)
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            return ProviderResponse(
                ok=False,
                error=f"HTTP {e.code}: {detail or e.reason}",
                latency_ms=int((datetime.now() - t0).total_seconds() * 1000),
                retry_after=retry_after,
            )
        except Exception as e:  # noqa: BLE001 — 统一吞异常返回错误响应
            return ProviderResponse(
                ok=False, error=str(e),
                latency_ms=int((datetime.now() - t0).total_seconds() * 1000),
            )


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API（OpenAI 格式消息入参 → 自动转换）"""

    def chat(self, messages: list, model: str = "", max_tokens: int = 0,
             temperature: float = 0.0) -> ProviderResponse:
        cfg = self.config
        model = model or cfg.model
        max_tokens = max_tokens or cfg.max_tokens
        temp = temperature if temperature > 0 else cfg.temperature
        url = cfg.api_base.rstrip("/") + "/v1/messages"
        system = ""
        msgs = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system = (system + "\n" + content).strip()
            elif role == "assistant":
                msgs.append({"role": "assistant", "content": content})
            else:
                msgs.append({"role": "user", "content": content})
        payload = {"model": model, "max_tokens": max_tokens, "messages": msgs}
        if temp > 0:
            payload["temperature"] = temp
        if system:
            payload["system"] = system
        headers = {
            "Content-Type": "application/json",
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            **cfg.headers,
        }
        t0 = datetime.now()
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            usage = data.get("usage", {})
            return ProviderResponse(
                ok=True, content=content, model=model, usage=usage,
                latency_ms=int((datetime.now() - t0).total_seconds() * 1000),
            )
        except Exception as e:  # noqa: BLE001
            return ProviderResponse(
                ok=False, error=str(e),
                latency_ms=int((datetime.now() - t0).total_seconds() * 1000),
            )


class ProviderRouter:
    """多 Provider 注册表 + 热切换（合并自 provider_router.py）"""

    def __init__(self):
        self._providers: dict[str, ProviderConfig] = {}
        self._active = "deepseek"
        self._build_defaults()

    def _build_defaults(self) -> None:
        # DeepSeek — 复用主服务 API_KEY（唯一事实来源，不再重复硬编码）
        self.register(ProviderConfig(
            name="deepseek", type=ProviderType.DEEPSEEK,
            api_base=DEEPSEEK_BASE, api_key=API_KEY, model="deepseek-chat", timeout=90,
        ))
        # Anthropic — 环境变量存在才自动注册（修复原 provider-router 硬编码/占位符失效问题）
        anth_key = os.environ.get("ANTHROPIC_API_KEY")
        if anth_key:
            self.register(ProviderConfig(
                name="anthropic", type=ProviderType.ANTHROPIC,
                api_base="https://api.anthropic.com", api_key=anth_key,
                model="claude-sonnet-4-20250514",
            ))
        if ZHIPU_API_KEY:
            # 智谱官方 · GLM-5.2 付费旗舰（1M ctx / 128K out；深度思考默认开，
            # 用 thinking={"type":"enabled"} + reasoning_effort high|max；冷启动慢，timeout 放宽）
            self.register(ProviderConfig(
                name="zhipu-glm52", type=ProviderType.OPENAI,
                api_base=ZHIPU_BASE, api_key=ZHIPU_API_KEY,
                model="glm-5.2", timeout=180,
                extra_payload={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            ))
            # 智谱官方 · GLM-5.3 最新旗舰（2026-08-19 上线，Intelligence Index 60，Agent 全球第二；
            # 同 GLM-5.2 参数，thinking={\"type\":\"enabled\"} + reasoning_effort high|max，1M ctx / 131K out）
            self.register(ProviderConfig(
                name="zhipu-glm53", type=ProviderType.OPENAI,
                api_base=ZHIPU_BASE, api_key=ZHIPU_API_KEY,
                model="glm-5.3", timeout=180,
                extra_payload={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            ))
        if SCNET_API_KEY:
            # SCNET（国家超算互联网）· GLM-5.2 托管（Token Plan 密钥 sk-tp- 开头；
            # 同智谱 GLM-5.2 推理参数，1M ctx / 128K out，冷启动慢 timeout 放宽；
            # 注意 SCNET 模型 ID 为大写 GLM-5.2（小写 glm-5.2 返回 422 Model Not Exist））
            self.register(ProviderConfig(
                name="scnet-glm52", type=ProviderType.OPENAI,
                api_base=SCNET_BASE, api_key=SCNET_API_KEY,
                model="GLM-5.2", timeout=180,
                extra_payload={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            ))
            # SCNET · DeepSeek-V4-Flash 托管（Token Plan；同样推理型模型 reasoning_tokens 计入
            # completion_tokens；SCNET 模型 ID 为大写 DeepSeek-V4-Flash）
            self.register(ProviderConfig(
                name="scnet-dsv4flash", type=ProviderType.OPENAI,
                api_base=SCNET_BASE, api_key=SCNET_API_KEY,
                model="DeepSeek-V4-Flash", timeout=120,
                extra_payload={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            ))
            # SCNET · Kimi-K3 月之暗面旗舰（Token Plan；Intelligence Index 60，1M ctx；
            # Token Plan 积分最贵（输出 171,429），固定月费下用一次赚一次；带 thinking 参数）
            self.register(ProviderConfig(
                name="scnet-kimik3", type=ProviderType.OPENAI,
                api_base=SCNET_BASE, api_key=SCNET_API_KEY,
                model="Kimi-K3", timeout=180,
                extra_payload={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            ))
            # SCNET · Qwen3.8-Max 通义旗舰（Token Plan；2.4T 参数 MoE，1M ctx；
            # 通义最新旗舰，思考/快速双模式；带 thinking 参数）
            self.register(ProviderConfig(
                name="scnet-qwen38max", type=ProviderType.OPENAI,
                api_base=SCNET_BASE, api_key=SCNET_API_KEY,
                model="Qwen3.8-Max", timeout=180,
                extra_payload={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            ))
        # 硅基流动 — 国内直连快（移除免费 Qwen2.5-7B，仅保留付费模型）
        if SILICONFLOW_API_KEY:
            # 硅基流动 · GLM-5.2 付费旗舰（消费代金券用；推理型模型冷启动慢，timeout 放宽）
            self.register(ProviderConfig(
                name="siliconflow-glm52", type=ProviderType.OPENAI,
                api_base=SILICONFLOW_BASE, api_key=SILICONFLOW_API_KEY,
                model="zai-org/GLM-5.2", timeout=180,
                extra_payload={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            ))
            # 硅基流动 · DeepSeek V4 Flash（settings.json 已声明 deepseek-ai/DeepSeek-V4-Flash；
            # 账户余额不足时 API 返回 30001，分层路由自动降级官方通道）
            self.register(ProviderConfig(
                name="siliconflow-dsv4flash", type=ProviderType.OPENAI,
                api_base=SILICONFLOW_BASE, api_key=SILICONFLOW_API_KEY,
                model="deepseek-ai/DeepSeek-V4-Flash", timeout=90,
            ))
        # 本地 Ollama（隐私/NDA 层；11434 常驻，无需手动启动）
        self.register(ProviderConfig(
            name="local", type=ProviderType.OPENAI,
            api_base=LOCAL_LLAMA_BASE, api_key="ollama", model="qwen2.5:3b",
            timeout=600, temperature=0.4,
        ))

    def register(self, cfg: ProviderConfig) -> None:
        self._providers[cfg.name] = cfg

    def switch(self, name: str) -> dict:
        if name not in self._providers:
            return {"ok": False, "error": f"未注册 Provider: {name}，可用: {list(self._providers)}"}
        self._active = name
        return {"ok": True, "active": name}

    @property
    def active_config(self) -> Optional[ProviderConfig]:
        return self._providers.get(self._active)

    @property
    def active_name(self) -> str:
        """当前活跃 Provider 名（status 面板用）"""
        return self._active

    @property
    def providers(self) -> dict:
        """全部已注册 Provider（status 面板用）"""
        return self._providers

    def list_providers(self) -> list[dict]:
        out = []
        for name, cfg in self._providers.items():
            out.append({
                "name": name, "type": cfg.type.value, "model": cfg.model,
                "active": name == self._active, "api_base": cfg.api_base,
            })
        return out

    def status(self) -> dict:
        return {"active": self._active, "providers": self.list_providers()}

    def chat(self, messages: list, model: str = "", max_tokens: int = 0) -> ProviderResponse:
        cfg = self.active_config
        if not cfg:
            return ProviderResponse(ok=False, error="无活跃 Provider")
        provider = (
            AnthropicProvider(cfg)
            if cfg.type == ProviderType.ANTHROPIC
            else OpenAICompatibleProvider(cfg)
        )
        return provider.chat(messages, model=model, max_tokens=max_tokens)

    def chat_via(self, name: str, messages: list, model: str = "",
                 max_tokens: int = 0, temperature: float = 0.0) -> ProviderResponse:
        """按名称直连指定 Provider（分层策略用），不切换活跃 Provider。"""
        cfg = self._providers.get(name)
        if not cfg:
            return ProviderResponse(ok=False, error=f"未注册 Provider: {name}")
        provider = (
            AnthropicProvider(cfg)
            if cfg.type == ProviderType.ANTHROPIC
            else OpenAICompatibleProvider(cfg)
        )
        return provider.chat(messages, model=model, max_tokens=max_tokens,
                             temperature=temperature)

    def recommend_model(self, effort: str = "high") -> dict:
        """effort → 模型推荐（单一事实来源: EFFORT_MODEL_MAP）"""
        return effort_to_model(effort)


provider_router = ProviderRouter()

# ── 运行时统计 ──
_stats = {
    "total_queries": 0,
    "routes": {"simple": 0, "complex": 0, "chan_theory": 0, "data_query": 0},
    "total_cost_yuan": 0.0,
    "prompt_cache_hit_tokens": 0,    # DeepSeek 前缀缓存命中 tokens（免费羊毛）
    "prompt_cache_miss_tokens": 0,   # 前缀缓存未命中 tokens
    "cache_saved_yuan": 0.0,         # 缓存命中累计省钱金额 (DSH 借鉴)
    "last_query": None,
}


def get_cost_stats() -> dict:
    """返回当前成本统计，供 deepcode-engine CostTracker 调用。"""
    return dict(_stats)


# ════════════════════════════════════════════════════════════════
# 🧠 智能分类器（Classifier）
# ════════════════════════════════════════════════════════════════

# ── Route 1: 日常数据（本地计算，不走 LLM）──
DATA_QUERY_PATTERNS = [
    # 日期 / 时间
    r"今天\s*(?:日期|星期|几号|周[一二三四五六日])",
    r"现在\s*(?:时间|几点)",
    r"当前\s*(?:时间|日期|时刻)",

    # 数学计算（含函数）
    r"^\s*[\d\s+\-*/().,%^]+\s*=\s*$",
    r"计算[：:]?\s*[\d\s+\-*/().,%^]+",
    r"\d+\s*[+\-*/%^]\s*\d+",
    r"(?:sqrt|sin|cos|tan|log|ln|abs|pow|ceil|floor|round)\s*\(",

    # 单位换算
    r"(?:换算|转换).*(?:元.*美元|美元.*元|公里.*英里|英里.*公里|千克.*磅|磅.*千克|摄氏.*华氏|华氏.*摄氏|斤.*公斤|公斤.*斤)",
    r"\d+\s*(?:元|美元|公里|英里|千克|磅|斤|公斤|厘米|英寸|摄氏度|华氏度)\s*(?:=|=|=|=|=)\s*\d+",

    # 简单格式化 / 转换
    r"大写[：:]?\s*\d+",
    r"\d+\s*(?:转|换)?\s*(?:大写|小写|罗马|二进制|十六进制|八进制)",

    # 统计 / 聚合（本地 pandas 就能算）
    r"(?:求和|平均|最大值|最小值|中位数|标准差|方差|累计|排序|去重|计数)[：:]?\s*[\d,\s]+",

    # 数据查询（实时市场数据走本地 akshare / yfinance）
    r"(?:股票|指数|基金|ETF|板块)\s*(?:行情|价格|代码|走势|涨幅|跌幅|成交量|成交额)",
    r"(?:上证|深证|创业板|科创板|沪深300|中证500|恒生|标普|纳斯达克|道指)\s*(?:指数|行情|涨跌|点数)?",
    r"(?:茅台|平安|腾讯|阿里|比亚迪|宁德|招行|五粮液)\s*(?:股价|行情|价格)",
    r"\d{6}\s*(?:行情|股价|价格|走势)",

    # 汇率
    r"(?:美元|欧元|英镑|日元|港币)\s*(?:兑|对|汇率|牌价)",
    r"汇率\s*(?:美元|欧元|英镑|日元|港币)",

    # 天气（如果有接口）
    r"(?:天气|温度|气温|湿度|空气质量)\s*(?:北京|上海|广州|深圳|杭州|成都)?",

    # 纯数字处理
    r"^\d{16,19}$",  # 可能是个卡号或ID
]

# ── Route 2: 缠论推演（强制 Pro + 缠论系统提示）──
CHAN_THEORY_PATTERNS = [
    r"缠论|chan\s*theory|chanlun|禅师",
    r"中枢|背驰|买卖点",
    r"笔[^记]|线段[^性]|级别[^:：]",
    r"区间套|三买|三卖|一买|一卖|二买|二卖",
    r"类[一二三]买|类[一二三]卖",
    r"走势终完美|中枢震荡|中枢延伸|中枢扩张|中枢新生",
    r"盘整背驰|趋势背驰|线段背驰",
    r"区间套[^:]|多级别联立|级别生长",
    r"周线.*日线.*[中枢背驰]|日线.*30分.*[中枢背驰]",
    r"缠中说禅",
]

# ── Route 3: 复杂分析（V4 Pro）──
COMPLEX_PATTERNS = [
    # 策略 / 回测
    r"策略.*(?:推导|设计|优化|评估|回测|构建)",
    r"backtest|sharpe|最大回撤|夏普|胜率|盈亏比|卡尔玛",
    r"多因子|因子.*(?:筛选|组合|加权|暴露|IC|IR)",
    r"量化.*(?:策略|模型|交易|框架|系统)",

    # 基本面深度
    r"杜邦|ROE|ROA|ROIC|毛利率|净利率|杠杆",
    r"财务.*(?:分析|建模|预测|报表|三表)",
    r"估值|DCF|贴现|FCFF|FCFE|WACC|CAPM",
    r"PE.*PB|PEG|EV/EBITDA|市净率|市盈率|市销率",

    # 技术分析深度
    r"威科夫|wyckoff|波浪|elliott|江恩|gann",
    r"吸筹|派发|拉升|出货|洗盘|试盘",
    r"主力|控盘|资金流向|北向|南向|龙虎榜",

    # 宏观经济
    r"宏观.*(?:分析|展望|预测|政策|周期)",
    r"GDP|CPI|PPI|PMI|社融|M2|LPR|MLF|逆回购",

    # 多步 / 综合推理
    r"综合.*(?:分析|判断|评估|结论|报告)",
    r"(?:首先|然后|最后|步骤).{10,}(?:首先|然后|最后)",
    r"对比.{2,}(?:股票|基金|公司|走势|基本面|技术|估值|和.{2,8})",

    # 专业身份
    r"作为.*(?:分析师|基金经理|量化研究员|交易员|首席)",

    # 组合建议
    r"资产配置|组合.*(?:优化|构建|调整|再平衡)",
    r"风险.*(?:评估|控制|敞口|对冲|VaR|压力测试)",

    # 代码生成（复杂）
    r"编写.{10,}(?:策略|回测|系统|程序|脚本)",
    r"实现.*(?:算法|模型|框架|系统)",
]

# ── 权重信号（额外加分）──
PRO_SIGNALS = [
    (r"[一-鿿]{800,}", 2),           # 中文 >800字
    (r"[a-zA-Z]{600,}", 1),          # 英文 >600字符
    (r"(?:报告|研报|分析[报告]|论文|白皮书)", 1),
    (r"请.*(?:详细|深入|全面|系统).{0,10}(?:分析|阐述|解释|说明)", 1),
]


def classify(query: str) -> dict:
    """
    智能分类请求 → 返回路由决策。

    Returns:
        {
            "route": "data_query" | "chan_theory" | "complex" | "simple",
            "reason": str,          # 中文原因
            "model": str | None,    # 使用的模型名
            "system_prompt": str,   # 系统提示词（LLM 路由用）
            "local": bool,          # 是否本地计算
        }
    """
    combined = query

    # ── Step 1: 优先检查缠论（最专业领域 → 不能被数据查询覆盖）──
    for pattern in CHAN_THEORY_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return {
                "route": "chan_theory",
                "reason": "检测到缠论分析需求，使用 V4 Pro 进行深度推演",
                "model": "deepseek-v4-pro",
                "system_prompt": _get_chan_system_prompt(),
                "local": False,
            }

    # ── Step 2: 检查是否为纯数据查询（本地计算，0 成本）──
    for pattern in DATA_QUERY_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return {
                "route": "data_query",
                "reason": "检测到数据/计算类请求，走本地计算（零成本）",
                "model": None,
                "system_prompt": "",
                "local": True,
            }

    # ── Step 3: 检查复杂任务特征 → V4 Pro ──
    trigger_hits = []
    for pattern in COMPLEX_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            trigger_hits.append(pattern)

    signal_score = 0
    for pattern, weight in PRO_SIGNALS:
        if re.search(pattern, combined, re.IGNORECASE):
            signal_score += weight

    if trigger_hits or signal_score >= 2:
        tags = _summarize_complex_triggers(trigger_hits) if trigger_hits else "综合复杂任务"
        return {
            "route": "complex",
            "reason": f"检测到复杂任务特征: {tags}（使用 V4 Pro 保障质量）",
            "model": "deepseek-v4-pro",
            "system_prompt": "你是一位资深金融分析师和量化研究员。请进行深入、系统、多角度的分析。",
            "local": False,
        }

    # ── Step 4: 兜底 → 简单任务 → V4 Flash ──
    return {
        "route": "simple",
        "reason": "日常问答，使用 V4 Flash 快速响应（省钱）",
        "model": "deepseek-v4-flash",
        "system_prompt": "你是一位有用的助手。请简洁、准确地回答问题。",
        "local": False,
    }


# ── 任务特征（安全/漏洞赏金 + 通用任务，分层路由用）──
# 命中 grunt 特征 → 打杂层（翻译/格式化/抽取/摘要/整理/文案）
GRUNT_PATTERNS = [
    r"(?:翻译|英译中|中译英|translate)",
    r"(?:格式化|排版|美化|整理成|转成|转换为?)(?:表格|JSON|Markdown|格式|报告)",
    r"(?:提取|抽取|解析).{0,10}(?:实体|字段|版本|参数|URL|链接|邮箱|IP)",
    r"(?:摘要|总结|概括|要点|标题化)",
    r"(?:去重|分类|归类|打标签|标签化|排序)",
    r"(?:改写|润色|措辞|措辞优化|报告模板|模板化)",
    r"(?:CVE|公告|披露).{0,10}(?:翻译|摘要|整理|总结)",
    r"(?:recon|侦察).{0,10}(?:整理|汇总|归类|去重|总结)",
    r"recon.{0,20}(?:notes?|整理|汇总)",
    r"(?:报告|report).{0,10}(?:排版|模板|格式)",
    # ── 通用特征（非安全任务也走 grunt 层）──
    r"(?:写|起草|撰写).{0,10}(?:邮件|通知|公告|文案|标语|标题|简介|新闻稿|会议纪要|演讲稿|讲稿)",
    r"(?:校对|纠错|简化|扩写|缩写).{0,10}(?:文本|文字|文章|内容|段落|句子)",
    r"(?:起名|命名|取名)",
    r"(?:生成|制作).{0,10}(?:表格|清单|列表|目录|大纲)",
    r"(?:把|将).{0,10}(?:内容|文本|笔记|资料|信息).{0,10}(?:整理|汇总|结构化|格式化|列成)",
    r"(?:朗读稿|讲稿|纪要)",
]

# 命中 code 特征 → 代码层（代码审查/PoC/模式匹配）
CODE_PATTERNS = [
    r"(?:代码|源码|code).{0,10}(?:审查|审计|review|分析|检查)",
    r"(?:审计|审查).{0,10}(?:代码|源码|函数|文件)",
    r"(?:PoC|poc|exploit|利用).{0,10}(?:起草|编写|生成|脚本|python|curl)",
    r"(?:漏洞|vuln).{0,10}(?:模式|特征|匹配|扫描结果)",
    r"(?:SQL注入|XSS|SSRF|CSRF|命令注入|文件上传|越权|IDOR|RCE|路径穿越|XXE|反序列化)",
    r"(?:数据流|污点|sink|source).{0,10}(?:分析|追踪)",
    r"(?:分析|review).{0,10}(?:补丁|diff|commit)",
    r"(?:waf|过滤器|bypass).{0,10}(?:绕过|方案|思路)",
    r"(?:写|编写|生成).{0,10}(?:脚本|工具|fuzzer|扫描器|爆破器)",
    r"(?:正则|regex).{0,10}(?:编写|生成|调试)",
    r"http.{0,30}(?:请求|payload|fuzz|参数)",
    # ── 通用特征（编程任务，与安全无关也走 code 层）──
    r"(?:写|编写|实现|开发|构建|封装).{0,10}(?:代码|程序|函数|接口|API|模块|组件|功能|脚本|类)",
    r"(?:修复|修一下|修好|修|解决).{0,10}(?:bug|缺陷|报错|错误|异常|问题)",
    r"(?:调试|排查|定位).{0,10}(?:代码|bug|报错|错误|异常|问题)",
    r"(?:写|编写|生成).{0,10}(?:测试|单元测试|测试用例|用例)",
    r"(?:重构|优化).{0,10}(?:代码|函数|模块|性能)",
    r"(?:SQL|查询|数据库).{0,10}(?:编写|优化|设计|调优)",
    r"(?:数据结构|算法).{0,10}(?:实现|编写|设计)",
    r"(?:部署|配置).{0,10}(?:Docker|脚本|yaml|yml|配置|CI|流水线|环境)",
    r"(?:代码|程序|工程).{0,10}(?:生成|编写|实现|补全|续写)",
]

# 命中 deep 特征 → 攻坚层（利用链/深度推理/逆向）
DEEP_PATTERNS = [
    r"(?:利用链|攻击链|攻击路径|exploit chain)",  # 独立出现即攻坚（不限位置）
    r"(?:推演|构造|设计|梳理|规划).{0,10}(?:利用|攻击|打|绕过)",
    r"(?:SSRF|反序列化|XXE|RCE|命令注入|文件上传).{0,20}(?:利用|攻击链|绕过|打内网|提权|getshell)",
    r"(?:利用链|exploit chain|攻击链|攻击路径).{0,20}(?:推演|分析|设计|构造)",
    r"(?:多步|复杂).{0,10}(?:逻辑|漏洞|推理|利用)",
    r"(?:逆向|反编译|decompile|反汇编|IDA|Ghidra|符号执行)",
    r"(?:密码学|加密|解密|哈希|签名|JWT|token).{0,20}(?:分析|破解|绕过|伪造)",
    r"(?:内存|堆|栈|格式化字符串|缓冲区溢出|堆溢出|UAF|double.?free)",
    r"(?:内核|驱动|KASLR|提权|权限提升|sandbox|沙箱).{0,20}(?:绕过|分析|利用)",
    r"(?:深度|全面|系统).{0,10}(?:推理|分析|评审).{0,10}(?:漏洞|报告|利用)",
    r"(?:研判|定级|评审|复现方案).{0,10}(?:漏洞|报告|发现)",
    r"(?:审计|review).{0,10}(?:智能合约|合约|solidity|二进制|固件)",
    r"(?:威胁建模|threat model|攻击面|attack surface).{0,20}(?:分析|评估)",
    # ── 通用特征（非安全的高难度推理也走 deep 攻坚层）──
    r"(?:算法|数学).{0,10}(?:推导|证明|分析|设计|复杂度)",
    r"(?:证明|推导|求证).{0,10}(?:定理|公式|命题|猜想)",
    r"数学.{0,10}(?:问题|难题|题)",
    r"(?:架构|系统).{0,10}(?:设计|方案|权衡|选型|评审|对比)",
    r"(?:设计|方案|权衡|选型|评审).{0,10}(?:架构|系统|框架)",
    r"(?:复杂|深度).{0,10}(?:推理|分析|论证|讨论).{0,10}(?:问题|方案|课题|议题)",
    r"(?:多步|嵌套|递推).{0,10}(?:逻辑|推理|计算|推演)",
    r"(?:严谨|严格).{0,10}(?:证明|论证|推导|分析)",
]


def classify_tier(query: str) -> dict:
    """安全/赏金任务 → 分层路由决策（grunt/code/main/deep）。

    与 classify() 互补：classify() 管通用 DeepSeek 路由（保持原语义），
    本函数管「分层策略」选层。优先级: deep > code > grunt > main。
    """
    q = query
    for pat in DEEP_PATTERNS:
        if re.search(pat, q, re.IGNORECASE):
            return {"tier": "deep", "reason": f"检测到攻坚特征: {pat[:40]}…"}
    for pat in CODE_PATTERNS:
        if re.search(pat, q, re.IGNORECASE):
            return {"tier": "code", "reason": f"检测到代码活特征: {pat[:40]}…"}
    for pat in GRUNT_PATTERNS:
        if re.search(pat, q, re.IGNORECASE):
            return {"tier": "grunt", "reason": f"检测到打杂特征: {pat[:40]}…"}
    return {"tier": "main", "reason": "未匹配安全特征，走主力层 V4 Flash"}


def _summarize_complex_triggers(hits: list[str]) -> str:
    tags = []
    for p in hits:
        if any(kw in p for kw in ["策略", "backtest", "sharpe", "因子", "量化"]):
            tags.append("策略/回测")
        elif any(kw in p for kw in ["杜邦", "ROE", "财务", "估值", "DCF", "PE"]):
            tags.append("基本面深度")
        elif any(kw in p for kw in ["威科夫", "波浪", "elliott", "江恩", "主力", "资金流向"]):
            tags.append("技术/资金分析")
        elif any(kw in p for kw in ["宏观", "GDP", "CPI", "PPI", "PMI", "M2"]):
            tags.append("宏观经济")
        elif any(kw in p for kw in ["综合", "对比"]):
            tags.append("综合/对比分析")
        elif any(kw in p for kw in ["资产配置", "组合", "风险", "VaR"]):
            tags.append("资产配置/风控")
        elif any(kw in p for kw in ["编写", "实现"]):
            tags.append("代码实现")
        elif any(kw in p for kw in ["作为"]):
            tags.append("专业分析模式")
    return " + ".join(dict.fromkeys(tags)) if tags else "综合复杂任务"


def _get_chan_system_prompt() -> str:
    return """你是一位精通缠论（缠中说禅技术理论）的资深交易员。
请严格基于缠论框架进行分析，包括但不限于：

1. **级别定位**：明确当前分析所属的级别（周线/日线/30分/5分/1分）
2. **笔与线段**：识别并标记关键笔、线段（包含顶底分型确认）
3. **中枢**：标注中枢区间（ZG、ZD、DD、GG），说明中枢级别
4. **背驰判断**：比较前后两段力度（MACD面积/高度），判断是否背驰
5. **买卖点**：识别三类买卖点，给出具体价格区间和依据
6. **区间套**：是否可用区间套精确定位转折点
7. **走势分类**：盘整/趋势，上升/下降/横盘
8. **策略建议**：基于当前缠论结构给出操作建议

输出格式：
```
📐 缠论分析报告
═══════════════
【级别】...
【笔/线段】...
【中枢】...
【背驰判断】...
【买卖点】...
【区间套】...
【走势分类】...
【策略建议】...
```"""


# ════════════════════════════════════════════════════════════════
# 💻 本地计算引擎（Local Computation Engine）
# ════════════════════════════════════════════════════════════════
# 处理日常数据查询，不走 LLM，零成本。
# ════════════════════════════════════════════════════════════════

async def local_compute(query: str) -> str:
    """
    本地计算入口：根据查询类型分发到对应的处理器。
    所有计算在本地完成，不调用任何 LLM API。
    """
    q = query.strip()

    # ── 日期 / 时间 ──
    if any(kw in q for kw in ["今天", "当前", "现在"]):
        return _handle_date_time(q)

    # ── 纯数学计算 ──
    if _is_math_query(q):
        return _handle_math(q)

    # ── 金融数据查询 ──
    if any(kw in q for kw in ["股票", "指数", "行情", "股价", "价格", "走势",
                               "上证", "深证", "创业板", "沪深300", "涨跌幅",
                               "基金", "ETF", "板块"]):
        return await _handle_finance_query(q)

    # ── 汇率 ──
    if any(kw in q for kw in ["汇率", "兑", "牌价", "美元", "欧元", "英镑", "日元", "港币"]):
        return await _handle_exchange_rate(q)

    # ── 单位换算 ──
    if any(kw in q for kw in ["换算", "转换", "等于多少"]):
        return _handle_conversion(q)

    # ── 文本处理 ──
    if any(kw in q for kw in ["大写", "小写", "统计", "字符数", "字数"]):
        return _handle_text_process(q)

    return f"⚠️ 检测到数据查询，但无法本地处理: {q}\n💡 请明确查询类型（如 '计算 2+2'、'今天日期'、'茅台股价'）"


def _handle_date_time(q: str) -> str:
    now = datetime.now()
    weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekday_names[now.weekday()]

    if "星期" in q or "周" in q:
        return f"📅 {now.strftime('%Y年%m月%d日')} {weekday}"
    if "时间" in q or "几点" in q:
        return f"🕐 {now.strftime('%H:%M:%S')}"
    return f"📅 {now.strftime('%Y年%m月%d日')} {weekday}  {now.strftime('%H:%M:%S')}"


def _is_math_query(q: str) -> bool:
    """判断是否为纯数学计算请求"""
    # 移除中文描述后检测数学表达式
    cleaned = re.sub(r"[计算结果等于得：: ]", "", q)
    # 简单的算术表达式
    if re.match(r'^[\d\s+\-*/().,%^e]+$', cleaned):
        return True
    # 包含"计算"关键词 + 数字和运算符
    if "计算" in q and re.search(r'\d\s*[+\-*/%^]\s*\d', q):
        return True
    # 数学函数
    if re.search(r'(?:sqrt|sin|cos|tan|log|ln|abs|pow|pi|e)\s*\(', q, re.IGNORECASE):
        return True
    return False


def _handle_math(q: str) -> str:
    """本地执行数学计算"""
    # 提取表达式
    expr = q
    if "计算" in expr:
        expr = expr.split("计算", 1)[-1].strip("：: ")
    # 清理：移除中文字符（除了小数点、正负号、运算符）
    expr = re.sub(r'[^\d\s+\-*/().,%^e]', '', expr).strip()
    expr = expr.rstrip('=').strip()

    if not expr:
        return "⚠️ 无法识别数学表达式"

    try:
        # 安全评估（仅允许数学运算）
        allowed_names = {
            "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
            "tan": math.tan, "log": math.log, "ln": math.log,
            "abs": abs, "pow": pow, "pi": math.pi, "e": math.e,
            "ceil": math.ceil, "floor": math.floor, "round": round,
        }
        result = eval(expr, {"__builtins__": {}}, allowed_names)
        # 格式化输出
        if isinstance(result, float):
            if abs(result) > 1e12 or (abs(result) < 1e-4 and result != 0):
                formatted = f"{result:.6e}"
            else:
                formatted = f"{result:,.6f}".rstrip("0").rstrip(".")
        else:
            formatted = str(result)
        return f"📐 计算结果\n\n  {expr} = **{formatted}**"
    except Exception as e:
        return f"⚠️ 计算错误: {str(e)}"


async def _handle_finance_query(q: str) -> str:
    """使用 akshare 本地获取金融数据"""
    try:
        import akshare as ak
    except ImportError:
        return "⚠️ akshare 未安装，无法获取金融数据。\n💡 安装: pip install akshare"

    try:
        # 识别查询的指数/股票
        index_map = {
            "上证指数": "sh000001", "上证": "sh000001",
            "深证成指": "sz399001", "深证": "sz399001",
            "创业板指": "sz399006", "创业板": "sz399006",
            "沪深300": "sh000300",
            "中证500": "sh000905",
            "科创50": "sh000688",
            "恒生指数": "hkHSI", "恒生": "hkHSI",
            "标普500": "sp500",
            "纳斯达克": "nasdaq",
            "道琼斯": "dowjones", "道指": "dowjones",
        }
        target_index = None
        for name, code in index_map.items():
            if name in q:
                target_index = code
                break

        if target_index:
            # 获取实时行情
            if target_index in ["sh000001", "sz399001", "sz399006", "sh000300",
                                "sh000905", "sh000688"]:
                df = await asyncio.to_thread(ak.stock_zh_index_daily, symbol=f"sh{target_index[2:]}" if target_index.startswith("sh") else target_index)
                if not df.empty:
                    latest = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else None
                    change = latest["close"] - prev["close"] if prev is not None else 0
                    change_pct = (change / prev["close"] * 100) if prev is not None and prev["close"] != 0 else 0
                    name = [k for k, v in index_map.items() if v == target_index][0]
                    return (
                        f"📊 {name} 实时行情\n"
                        f"{'─' * 40}\n"
                        f"  最新价: {latest['close']:.2f}\n"
                        f"  涨跌额: {change:+.2f}\n"
                        f"  涨跌幅: {change_pct:+.2f}%\n"
                        f"  最高:   {latest['high']:.2f}\n"
                        f"  最低:   {latest['low']:.2f}\n"
                        f"  日期:   {latest.name}\n"
                    )
            elif target_index == "hkHSI":
                # 尝试获取港股行情
                df = await asyncio.to_thread(ak.stock_hk_index_daily, symbol="HSI")
                if not df.empty:
                    latest = df.iloc[-1]
                    return f"📊 恒生指数\n{'─' * 40}\n  最新: {latest['close']}\n  日期: {latest.name}"

            return f"⚠️ 暂不支持该指数实时查询（{target_index}）"

        # 如果是具体股票代码
        stock_codes = re.findall(r'\b\d{6}\b', q)
        if stock_codes:
            code = stock_codes[0]
            df = await asyncio.to_thread(ak.stock_zh_a_spot_em)
            match = df[df["代码"] == code]
            if not match.empty:
                row = match.iloc[0]
                return (
                    f"📊 {row.get('名称', code)} ({code}) 实时行情\n"
                    f"{'─' * 40}\n"
                    f"  最新价: {row.get('最新价', 'N/A')}\n"
                    f"  涨跌幅: {row.get('涨跌幅', 'N/A')}%\n"
                    f"  涨跌额: {row.get('涨跌额', 'N/A')}\n"
                    f"  今开:   {row.get('今开', 'N/A')}\n"
                    f"  昨收:   {row.get('昨收', 'N/A')}\n"
                    f"  最高:   {row.get('最高', 'N/A')}\n"
                    f"  最低:   {row.get('最低', 'N/A')}\n"
                    f"  成交量: {row.get('成交量', 'N/A')}\n"
                    f"  成交额: {row.get('成交额', 'N/A')}\n"
                )

        # 股票名称查询
        stock_names = re.findall(r'[茅台|平安|腾讯|阿里|比亚迪|宁德|招行|五粮液]', q)
        if stock_names:
            df = await asyncio.to_thread(ak.stock_zh_a_spot_em)
            for _, row in df.iterrows():
                name = str(row.get("名称", ""))
                for sn in stock_names:
                    if sn in name:
                        return (
                            f"📊 {name} ({row.get('代码', '')}) 实时行情\n"
                            f"{'─' * 40}\n"
                            f"  最新价: {row.get('最新价', 'N/A')}\n"
                            f"  涨跌幅: {row.get('涨跌幅', 'N/A')}%\n"
                            f"  涨跌额: {row.get('涨跌额', 'N/A')}\n"
                            f"  今开:   {row.get('今开', 'N/A')}\n"
                            f"  昨收:   {row.get('昨收', 'N/A')}\n"
                            f"  最高:   {row.get('最高', 'N/A')}\n"
                            f"  最低:   {row.get('最低', 'N/A')}\n"
                        )

        return "⚠️ 未找到相关股票数据，请提供正确的股票代码或名称"

    except Exception as e:
        return f"⚠️ 获取金融数据失败: {str(e)[:200]}"


async def _handle_exchange_rate(q: str) -> str:
    """通过免费 API 获取实时汇率"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.exchangerate-api.com/v4/latest/CNY")
            if resp.status_code == 200:
                data = resp.json()
                rates = data.get("rates", {})
                # 常用汇率
                result = "💱 实时汇率（基于 CNY）\n"
                result += f"{'─' * 40}\n"
                pairs = [
                    ("USD", "美元"), ("EUR", "欧元"), ("GBP", "英镑"),
                    ("JPY", "日元(100)"), ("HKD", "港币"), ("KRW", "韩元(100)"),
                    ("AUD", "澳元"), ("CAD", "加元"), ("CHF", "瑞郎"),
                    ("SGD", "新元"), ("THB", "泰铢"),
                ]
                for code, name in pairs:
                    if code in rates:
                        rate = rates[code]
                        if code in ("JPY", "KRW"):
                            rate = rate * 100
                            result += f"  {name}: {rate:.2f}\n"
                        else:
                            result += f"  {name}: {rate:.4f}\n"
                result += f"\n📅 {data.get('date', '')}"
                return result
            else:
                return "⚠️ 汇率服务暂时不可用"
    except Exception as e:
        return f"⚠️ 获取汇率失败: {str(e)[:100]}"


def _handle_conversion(q: str) -> str:
    """单位换算"""
    # 人民币 ↔ 美元（使用最近汇率 ~7.25）
    usd_cny = 7.25
    m = re.search(r'(\d+\.?\d*)\s*元.*?美元', q)
    if m:
        amount = float(m.group(1))
        return f"💱 {amount} 元 = {amount / usd_cny:.4f} 美元（参考汇率 1 USD = {usd_cny} CNY）"
    m = re.search(r'(\d+\.?\d*)\s*美元.*?元', q)
    if m:
        amount = float(m.group(1))
        return f"💱 {amount} 美元 = {amount * usd_cny:.2f} 元（参考汇率 1 USD = {usd_cny} CNY）"

    return "⚠️ 暂不支持该换算类型"


def _handle_text_process(q: str) -> str:
    """文本处理"""
    return "📝 文本处理功能\n\n将文本粘贴到 prompt 中即可统计字数、大小写转换等。\n示例: '统计: 这是一段文本' → 返回字数"


# ════════════════════════════════════════════════════════════════
# 🤖 DeepSeek LLM 调用层
# ════════════════════════════════════════════════════════════════

async def call_deepseek(
    model_key: str,
    prompt: str,
    system: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.6,
) -> dict:
    """调用 LLM API (OpenAI-compatible) — 按模型分发端点（deepseek 官方 / glm-5.2 → SCNET）"""
    model_id = MODEL_ID_MAP.get(model_key, model_key)

    # GLM-5.2 → SCNET 托管端点（Token Plan）；其余走 DeepSeek 官方
    if model_key in ("glm-5.2", "GLM-5.2"):
        base = SCNET_BASE
        api_key = SCNET_API_KEY
    else:
        base = DEEPSEEK_BASE
        api_key = API_KEY

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=180) as client:
        try:
            resp = await client.post(
                f"{base}/chat/completions",
                headers=headers,
                json={
                    "model": model_id,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": 0.95,
                },
            )
        except httpx.RequestError as e:
            return {"error": "网络连接失败", "detail": str(e)[:300]}

        if resp.status_code != 200:
            detail = resp.text[:500]
            if resp.status_code == 401:
                hint = "API Key 无效或已过期"
            elif resp.status_code == 402:
                hint = "余额不足"
            elif resp.status_code == 429:
                hint = "请求频率超限"
            else:
                hint = f"HTTP {resp.status_code}"
            return {"error": hint, "detail": detail}

        data = resp.json()
        choice = data["choices"][0]
        msg = choice.get("message", {})
        reasoning = msg.get("reasoning_content", "")
        content = msg.get("content") or reasoning or "(empty)"
        usage = data.get("usage", {})

        # 计算成本（DeepSeek 前缀缓存命中部分按 cache_hit 价计费，未命中按 in 价）
        costs = MODEL_COSTS.get(model_key, {"in": 1, "out": 2})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cache_hit = usage.get("prompt_cache_hit_tokens", 0)
        cache_miss = max(prompt_tokens - cache_hit, 0)
        cache_price = costs.get("cache_hit", costs["in"] * 0.1)  # 无精确 cache 价的模型按 1/10 近似
        cost = (cache_miss * costs["in"] + cache_hit * cache_price + completion_tokens * costs["out"]) / 1_000_000
        # 缓存省钱量化 (DSH 借鉴): 假设全部未命中需付的全价 vs 实际花费
        full_cost = (prompt_tokens * costs["in"] + completion_tokens * costs["out"]) / 1_000_000
        cache_saved = max(full_cost - cost, 0.0)

        return {
            "content": content,
            "reasoning": reasoning,
            "model": data.get("model", model_id),
            "usage": usage,
            "cost_yuan": cost,
            "cache_hit_tokens": cache_hit,
            "cache_miss_tokens": cache_miss,
            "cache_saved_yuan": cache_saved,
            "finish_reason": choice.get("finish_reason", ""),
        }


def format_llm_output(result: dict, route_info: dict) -> str:
    """格式化 LLM 输出 — 合并自 deepseek-direct 的格式化风格"""
    model_key = route_info["model"]
    costs = MODEL_COSTS.get(model_key, {"emoji": "🤖", "label": model_key, "note": ""})
    emoji = costs["emoji"]
    label = costs["label"]
    note = costs.get("note", "")

    out = f"【{emoji} {label} · 智能路由】\n"
    out += f"🔄 {route_info['reason']}\n"

    reasoning = result.get("reasoning", "")
    if reasoning:
        if len(reasoning) > 800:
            reasoning = reasoning[:800] + "\n... (推理链截断，完整版需增加 max_tokens)"
        out += f"\n🧠 推理链:\n{reasoning}\n\n{'─' * 50}\n\n📝 结论:\n"

    out += f"{result['content']}\n\n"
    out += f"{'─' * 50}\n"

    usage = result.get("usage", {})
    cost = result.get("cost_yuan", 0)
    out += f"📊 模型: {result.get('model', '?')}\n"
    out += f"📊 Token: 入{usage.get('prompt_tokens', 0)} + 出{usage.get('completion_tokens', 0)}"
    out += f" = {usage.get('total_tokens', 0)}\n"
    # DeepSeek 前缀缓存命中信息（免费羊毛：命中部分价格大幅降低）
    hit_t = result.get("cache_hit_tokens", 0)
    if hit_t:
        miss_t = result.get("cache_miss_tokens", 0)
        total_prompt = hit_t + miss_t
        hit_rate = hit_t / total_prompt * 100 if total_prompt > 0 else 0.0
        saved_yuan = result.get("cache_saved_yuan", 0.0)
        out += f"⚡ 前缀缓存: 命中 {hit_t} / 未命中 {miss_t} tokens (命中率 {hit_rate:.1f}%)\n"
        if saved_yuan > 0:
            out += f"💸 缓存省钱: ¥{saved_yuan:.4f} (全价 ¥{saved_yuan + cost:.4f} → 实付 ¥{cost:.4f})\n"
    out += f"💰 成本: ¥{cost:.4f} {note}\n"
    out += f"🎯 路由: {emoji} {route_info['route']}"

    return out


# ════════════════════════════════════════════════════════════════
# 🛠️ MCP 工具
# ════════════════════════════════════════════════════════════════

@mcp.tool()
async def router_query(
    query: str,
    max_tokens: int = 0,
    temperature: float = 0.6,
    force_route: str = "",
    force_model: str = "",
    effort: str = "",
) -> str:
    """
    🎯 通用智能路由 — 自动选择最优处理路径。

    根据输入内容自动判断:
    - 简单问答 → V4 Flash (¥1)    ⚡
    - 复杂分析 → V4 Pro   (¥3)    🧠
    - 缠论推演 → V4 Pro   (¥3)    📐
    - 日常数据 → 本地计算 (¥0)    💻
    - 深度推理 → R1       (¥4)    🔴

    Args:
        query:       用户输入的问题或指令
        max_tokens:  最大输出 Token（0=自动）
        temperature: 温度参数 (0.0-1.0，默认 0.6)
        force_route: 强制指定路由: "simple" | "complex" | "chan_theory" | "data_query" | ""
        force_model: 强制指定模型: "deepseek-v4-pro" | "deepseek-v4-flash" | "deepseek-r1" | ""
                     优先级最高（> effort > 自动路由）
        effort:      期望思考深度: "low" | "medium" | "high" | "xhigh" | "max"
                     （合并自 provider-router 的 effort→模型 映射；优先级低于 force_model，高于自动分类）

    Returns:
        处理结果 + 路由信息 + 成本
    """
    _stats["total_queries"] += 1
    _stats["last_query"] = query[:100]

    # 路由决策
    if force_route:
        route_info = _force_route(force_route, query)
    else:
        route_info = classify(query)

    # force_model 优先级最高 — 覆盖路由决策的模型选择
    if force_model and force_model in MODEL_COSTS:
        costs = MODEL_COSTS[force_model]
        route_info["model"] = force_model
        route_info["reason"] = f"🔧 用户强制指定: {costs['emoji']} {costs['label']}"
        # 如果不走本地计算且没有 system_prompt，给个默认
        if not route_info["local"] and not route_info.get("system_prompt"):
            route_info["system_prompt"] = "你是一位有用的助手。"

    # effort 优先级第二 — 由调用方指定期望思考深度（合并自 provider-router）
    elif effort:
        mapped = effort_to_model(effort)
        if mapped:
            route_info["model"] = mapped["model"]
            route_info["reason"] = f"🎚️ effort={effort} → {MODEL_COSTS[mapped['model']]['emoji']} {MODEL_COSTS[mapped['model']]['label']}"
            if not route_info["local"] and not route_info.get("system_prompt"):
                route_info["system_prompt"] = "你是一位有用的助手。"

    # 更新统计
    _stats["routes"][route_info["route"]] += 1

    # ── 本地计算 ──
    if route_info["local"]:
        result = await local_compute(query)
        _stats["total_cost_yuan"] += 0.0
        header = (
            f"💻 [本地计算 · 零成本]\n"
            f"🔄 {route_info['reason']}\n\n"
        )
        return header + result

    # ── 语义缓存 + 本地小脑级联（仅自动路由 + simple，force 时跳过）──
    if cascade and not force_route and not force_model and route_info["route"] == "simple":
        cached = cascade.cache_lookup(query, route=route_info["route"])
        if cached:
            _stats["total_cost_yuan"] += 0.0
            header = (
                f"⚡ [语义缓存命中 · 零成本]\n"
                f"🔄 相似度 {cached['similarity']:.2f} 直接返回历史答案\n\n"
            )
            return header + cached["response"]

        local = cascade.cascade_answer(query, temperature=min(temperature, 0.5))
        if local:
            _stats["total_cost_yuan"] += 0.0
            cascade.cache_store(query, local["response"], route=route_info["route"])
            header = (
                f"🧠 [本地小脑 · 零成本]\n"
                f"🔄 qwen3:4b 本地回答通过信任评估 (评分 {local['score']:.2f})\n\n"
            )
            return header + local["response"]

    # ── LLM 调用 ──
    if max_tokens == 0:
        max_tokens = 8192 if route_info["route"] in ("complex", "chan_theory") else 2048

    # 进云端前的长输入压缩（仅自动路由; 确定性规则, 不破坏 DeepSeek 前缀缓存）
    # 用独立变量, 不覆盖 query (缓存 key 保持原始用户问题)
    prompt_to_send = query
    if cascade and not force_route and not force_model:
        compressed = cascade.compress_prompt(query)
        if compressed != query:
            prompt_to_send = compressed
            route_info["reason"] = (route_info.get("reason") or "") + " + 输入已压缩"

    result = await call_deepseek(
        model_key=route_info["model"],
        prompt=prompt_to_send,
        system=route_info.get("system_prompt", ""),
        max_tokens=max_tokens,
        temperature=temperature,
    )

    if "error" in result:
        _stats["total_cost_yuan"] += 0.0
        return (
            f"❌ [Router · {route_info['model']}] {result['error']}\n"
            f"详情: {result.get('detail', '')}\n"
            f"路由: {route_info['route']} → {route_info['reason']}"
        )

    cost = result.get("cost_yuan", 0)
    _stats["total_cost_yuan"] += cost

    # DeepSeek 前缀缓存统计累计（免费羊毛：命中部分价格大幅降低）
    _stats["prompt_cache_hit_tokens"] += result.get("cache_hit_tokens", 0)
    _stats["prompt_cache_miss_tokens"] += result.get("cache_miss_tokens", 0)
    # 缓存省钱累计 (DSH 借鉴: 全价 vs 实付差价, 在 chat() 已按模型单价精确计算)
    _stats["cache_saved_yuan"] += result.get("cache_saved_yuan", 0.0)

    # 云端输出去水 + 写入语义缓存（仅 simple 自动路由；去水失败自动降级返回原文）
    if cascade and not force_route and not force_model and route_info["route"] == "simple":
        content = result.get("content", "")
        dewatered = cascade.dewater_response(content)
        if dewatered and dewatered != content:
            result["content"] = dewatered
        cascade.cache_store(query, result.get("content", ""), route=route_info["route"])

    return format_llm_output(result, route_info)


def _force_route(route: str, query: str) -> dict:
    """强制指定路由"""
    routes = {
        "simple": {
            "route": "simple", "model": "deepseek-v4-flash",
            "system_prompt": "请简洁回答。", "local": False,
            "reason": "用户强制指定: 简单问答模式",
        },
        "complex": {
            "route": "complex", "model": "deepseek-v4-pro",
            "system_prompt": "你是一位资深分析师，请进行深入分析。", "local": False,
            "reason": "用户强制指定: 复杂分析模式",
        },
        "chan_theory": {
            "route": "chan_theory", "model": "deepseek-v4-pro",
            "system_prompt": _get_chan_system_prompt(), "local": False,
            "reason": "用户强制指定: 缠论推演模式",
        },
        "data_query": {
            "route": "data_query", "model": None,
            "system_prompt": "", "local": True,
            "reason": "用户强制指定: 本地计算模式",
        },
    }
    return routes.get(route, routes["simple"])


@mcp.tool()
async def router_status() -> str:
    """
    🩺 Router MCP 状态面板 — 查看路由策略、成本、统计。

    Returns:
        完整状态报告
    """
    out = "═" * 60 + "\n"
    out += "🎯 Router MCP · 通用智能路由引擎\n"
    out += "═" * 60 + "\n\n"

    out += "📋 路由策略:\n"
    out += f"  {'💻 日常数据':<20} → 本地计算（akshare/pandas/httpx）  ¥0.0000\n"
    out += f"  {'⚡ 简单问答':<20} → V4 Flash 快速响应              ¥0.001~0.01\n"
    out += f"  {'🧠 复杂分析':<20} → V4 Pro 深度推理                ¥0.01~0.03\n"
    out += f"  {'📐 缠论推演':<20} → V4 Pro + 缠论系统提示          ¥0.01~0.03\n"
    out += f"  {'🔴 深度推理':<20} → R1 超长CoT（兼容保留）         ¥0.004~0.02\n\n"

    out += "─" * 60 + "\n"
    out += "📋 可用模型:\n"
    for key, info in MODEL_COSTS.items():
        out += f"  {info['emoji']} {info['label']} (ID: {MODEL_ID_MAP.get(key, '?')})\n"
        out += f"     成本: ¥{info['in']}/¥{info['out']} per 1M tokens {info.get('note', '')}\n\n"

    out += "─" * 60 + "\n"
    out += "📊 运行统计:\n"
    out += f"  总请求数: {_stats['total_queries']}\n"
    out += f"  总花费:   ¥{_stats['total_cost_yuan']:.4f}\n"
    if _tier_stats["total"] > 0:
        out += f"\n  🪜 分层策略: 调用 {_tier_stats['total']} 次"
        out += f"（免费 {_tier_stats['free_calls']} / 付费 {_tier_stats['paid_calls']}）"
        out += f" · 成本 ¥{_tier_stats['cost_yuan']:.4f} · 降级 {_tier_stats['fallbacks']} 次\n"
        out += "  → router_tier_status() 查看分层详情\n"
    hit_tokens = _stats.get("prompt_cache_hit_tokens", 0)
    miss_tokens = _stats.get("prompt_cache_miss_tokens", 0)
    total_prompt = hit_tokens + miss_tokens
    if total_prompt > 0:
        hit_rate = hit_tokens / total_prompt * 100
        out += f"  ⚡ DeepSeek 前缀缓存: 命中 {hit_tokens} / 未命中 {miss_tokens} tokens (命中率 {hit_rate:.1f}%)\n"
        saved_yuan = _stats.get("cache_saved_yuan", 0.0)
        if saved_yuan > 0:
            out += f"  💸 缓存累计省钱: ¥{saved_yuan:.4f}\n"
    for route, count in _stats["routes"].items():
        emoji = {"simple": "⚡", "complex": "🧠", "chan_theory": "📐", "data_query": "💻"}
        out += f"  {emoji.get(route, '❓')} {route}: {count} 次\n"

    if _stats["last_query"]:
        out += f"\n  最近查询: \"{_stats['last_query']}\"\n"

    # 级联 + 语义缓存状态（模块缺失时跳过）
    if cascade is not None:
        try:
            cs = cascade.cascade_stats()
            out += "\n  🧠 本地小脑级联:\n"
            out += f"    本地回答(信任通过): {cs.get('local_answers', 0)} 次\n"
            out += f"    升级云端:           {cs.get('local_escalated', 0)} 次\n"
            out += "  ⚡ 语义缓存 (bge-m3):\n"
            out += f"    命中: {cs.get('cache_hits', 0)} 次 | 存储: {cs.get('cache_stored', 0)} 条\n"
        except Exception:  # noqa: BLE001 — 统计异常不影响状态面板
            out += "\n  🧠 本地小脑级联: 状态读取失败\n"
    else:
        out += "\n  🧠 本地小脑级联: 未启用（router_cascade 缺失）\n"

    # ── Provider 抽象层状态（合并自 provider-router）──
    out += "\n" + "─" * 60 + "\n"
    out += "🔌 Provider 抽象层（多 Provider 路由）:\n"
    out += f"  当前活跃: {provider_router.active_name}\n"
    for name, prov in provider_router.providers.items():
        marker = "◀ 活跃" if name == provider_router.active_name else ""
        out += f"  - {name} [{prov.type.value}]  model={prov.model}  {marker}\n"
    out += "  切换: router_provider_switch(name) | 列表: router_provider_list()\n"
    out += "  推荐: router_provider_recommend(effort) | 直聊: router_provider_chat()\n"

    out += "\n" + "─" * 60 + "\n"
    out += "🔧 使用:\n"
    out += "  router_query(query)                              → 自动路由（推荐）\n"
    out += "  router_query(query, force_route='complex')       → 强制复杂分析\n"
    out += "  router_query(query, force_route='data_query')    → 强制本地计算\n"
    out += "  router_query(query, force_model='deepseek-v4-pro')   → 强制 V4 Pro\n"
    out += "  router_query(query, force_model='deepseek-r1')       → 强制 R1 推理\n"
    out += "  router_query(query, effort='high')               → 按思考深度选模型\n"
    out += "  router_provider_recommend('xhigh')               → 推荐模型 + max_tokens\n"
    out += "  router_deepseek_status()                         → API 健康检查\n"
    out += "  router_analyze_breakdown()                       → 成本分析\n"
    out += "  router_tier_query(query, tier='auto')            → 分层路由（纯付费）\n"
    out += "  router_tier_status()                             → 分层策略面板\n"

    return out


@mcp.tool()
async def router_analyze_breakdown() -> str:
    """
    💰 Router MCP 成本分析 — 详细的花费明细和节省估算。

    Returns:
        成本分析报告
    """
    from collections import Counter

    out = "═" * 60 + "\n"
    out += "💰 Router MCP · 成本分析报告\n"
    out += "═" * 60 + "\n\n"

    total = _stats["total_queries"]
    if total == 0:
        out += "暂无数据，先使用 router_query 后再来查看。\n"
        return out

    routes = _stats["routes"]
    total_cost = _stats["total_cost_yuan"]

    # 估算：如果全部用 Pro 会花多少
    hypothetical_all_pro = total * 0.015  # 假设平均每次 ¥0.015
    # 全部用 Flash
    hypothetical_all_flash = total * 0.003

    saved_vs_pro = hypothetical_all_pro - total_cost
    saved_vs_half = (hypothetical_all_pro + hypothetical_all_flash) / 2 - total_cost

    out += f"📊 总查询: {total} 次\n"
    out += f"💰 实际总花费: ¥{total_cost:.4f}\n\n"

    # DeepSeek 前缀缓存省钱估算（命中价 vs 输入价的精确差价，v4-flash 命中 ¥0.02 比输入 ¥1 省 98%）
    hit_tokens = _stats.get("prompt_cache_hit_tokens", 0)
    miss_tokens = _stats.get("prompt_cache_miss_tokens", 0)
    if hit_tokens > 0:
        total_prompt = hit_tokens + miss_tokens
        hit_rate = hit_tokens / total_prompt * 100 if total_prompt > 0 else 0.0
        ref = MODEL_COSTS.get("deepseek-v4-flash", {"in": 1})
        ref_cache = ref.get("cache_hit", ref["in"] * 0.1)  # 参考模型 V4 Flash 的缓存命中价
        cache_saved = hit_tokens * (ref["in"] - ref_cache) / 1_000_000  # 精确差价 = 输入价 - 命中价
        out += "⚡ DeepSeek 前缀缓存:\n"
        out += f"  命中 {hit_tokens} / 未命中 {miss_tokens} tokens (命中率 {hit_rate:.1f}%)\n"
        out += f"  缓存省钱 ≈ ¥{cache_saved:.4f}（V4 Flash 精确差价 ¥{ref['in'] - ref_cache}/M）\n\n"

    out += "📈 节省对比:\n"
    out += f"  方案               总花费        对比实际\n"
    out += f"  {'─' * 55}\n"
    out += f"  ✅ Router MCP（智能路由） ¥{total_cost:.4f}     —\n"
    out += f"  ❌ 全部用 V4 Pro       ¥{hypothetical_all_pro:.4f}     +¥{saved_vs_pro:.4f}\n"
    out += f"  ⚡ 全部用 V4 Flash     ¥{hypothetical_all_flash:.4f}     -¥{total_cost - hypothetical_all_flash:.4f}\n\n"

    out += "🏆 路由分配:\n"
    route_pct = {k: v / total * 100 for k, v in routes.items() if v > 0}
    for route, pct in sorted(route_pct.items(), key=lambda x: -x[1]):
        emoji = {"simple": "⚡", "complex": "🧠", "chan_theory": "📐", "data_query": "💻"}
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        out += f"  {emoji.get(route, '❓')} {route:<12} {bar} {pct:.0f}% ({routes[route]}次)\n"

    out += f"\n💡 路由引擎已经为您节省 ¥{saved_vs_pro:.2f}（对比全用 Pro）\n"

    return out


@mcp.tool()
async def router_recommend_mcp(task: str) -> str:
    """
    🧩 MCP 工具推荐 — 根据任务描述推荐最佳 MCP 工具。

    例如：
      "读取一个文件"        → filesystem
      "操作 SQLite 数据库"  → sqlite
      "分析股票行情"        → china-stock / akshare
      "打开浏览器搜索"      → playwright

    Args:
        task: 任务描述（自然语言）

    Returns:
        推荐的 MCP 工具列表 + 用法
    """
    task_lower = task.lower()
    recs = []

    # 文件操作
    if any(k in task_lower for k in ["文件", "读取", "写入", "创建", "目录", "路径",
                                      "file", "read", "write", "directory"]):
        recs.append(("📁 filesystem", "read_file / write_file / edit_file / search_files",
                     "所有文件操作"))

    # 浏览器
    if any(k in task_lower for k in ["浏览器", "网页", "打开网址", "截图", "搜索",
                                      "browser", "web", "page", "url", "http"]):
        recs.append(("🌐 playwright (browser)", "browser_navigate / browser_click / browser_snapshot",
                     "浏览器自动化"))

    # 数据库
    if any(k in task_lower for k in ["数据库", "sql", "查询", "表", "数据",
                                      "database", "query", "select", "sqlite"]):
        recs.append(("🗄️ sqlite", "query / execute / describe-table / list-tables",
                     "SQLite 数据库操作"))
        recs.append(("🐤 duckdb", "execute_query / import_csv / export_csv",
                     "DuckDB 分析引擎（适合 CSV/数据分析）"))

    # 金融
    if any(k in task_lower for k in ["股票", "行情", "k线", "A股", "交易",
                                      "stock", "finance", "market", "trading"]):
        recs.append(("📈 china-stock / akshare", "股市数据查询",
                     "A股/港股实时行情"))
        recs.append(("📊 tradingview", "技术图表分析"))

    # Git
    if any(k in task_lower for k in ["git", "提交", "仓库", "推送", "commit",
                                      "push", "pull", "branch"]):
        recs.append(("🔧 github", "create_or_update_file / search_repositories / list_commits",
                     "GitHub API 操作"))

    # AI / LLM
    if any(k in task_lower for k in ["ai", "llm", "deepseek", "模型", "分析",
                                      "chat", "gpt", "推理"]):
        recs.append(("🧠 deepseek-direct", "deepseek_direct_analyze / deepseek_reasoner_analyze",
                     "DeepSeek 直连 API"))
        recs.append(("🎯 router-mcp", "router_query（自动路由）",
                     "通用智能路由"))

    # 压缩/优化
    if any(k in task_lower for k in ["压缩", "优化", "token", "节省",
                                      "compress", "optimize"]):
        recs.append(("🔧 headroom", "headroom_compress / headroom_status",
                     "HEADROOM Token 压缩代理"))

    # AutoCAD
    if any(k in task_lower for k in ["cad", "autocad", "绘图", "图纸",
                                      "draw", "dwg"]):
        recs.append(("📐 autocad", "draw_entities / manage_layers / manage_files",
                     "AutoCAD 自动绘图"))

    # Windows 应用
    if any(k in task_lower for k in ["windows", "win", "桌面", "窗口",
                                      "桌面应用", "uia"]):
        recs.append(("🪟 winapp", "click_element / get_snapshot / type_text",
                     "Windows 桌面应用自动化"))

    # Office
    if any(k in task_lower for k in ["word", "docx", "文档", "office",
                                      "表格", "报告"]):
        recs.append(("📝 office-pro", "create_document / insert_paragraph / add_table",
                     "Office 文档生成"))

    # Notion
    if any(k in task_lower for k in ["notion", "笔记"]):
        recs.append(("📓 notion", "API-post-search / API-retrieve-a-page / API-patch-page",
                     "Notion API"))

    if not recs:
        return "🤷 未匹配到特定 MCP 工具，建议使用 router_query 通用路由。"

    result = f"## 🧩 推荐 MCP 工具\n\n根据任务「{task}」，推荐以下工具：\n\n"
    for i, (name, usage, desc) in enumerate(recs, 1):
        result += f"{i}. **{name}**\n   - {usage}\n   - {desc}\n\n"
    result += "---\n💡 在 Deep Code 中直接描述任务，系统会自动选择合适的工具。"
    return result


@mcp.tool()
async def router_quick_calc(expression: str) -> str:
    """
    🧮 快速本地计算 — 纯本地执行，零成本、零延迟。

    Args:
        expression: 数学表达式，例如 "2+2", "sqrt(144)", "pi*3^2"

    Returns:
        计算结果
    """
    return _handle_math(expression)


@mcp.tool()
async def router_deepseek_status() -> str:
    """
    🩺 DeepSeek API 健康检查 — 测试直连连通性 + 显示所有可用模型。

    Returns:
        API 连接状态 + 可用模型 + 路由规则
    """
    # 用 Flash 做健康检查（最便宜）
    result = await call_deepseek(
        "deepseek-v4-flash",
        "回复'OK'即可",
        max_tokens=5,
        temperature=0.0,
    )

    out = "═" * 60 + "\n"
    out += "🩺 DeepSeek API · 智能路由引擎\n"
    out += "═" * 60 + "\n\n"

    if "error" in result:
        out += f"❌ 连接失败: {result['error']}\n"
        out += f"   详情: {result.get('detail', '')[:200]}\n"
        out += f"\n💡 建议:\n"
        out += f"   1. 检查 DEEPSEEK_API_KEY\n"
        out += f"   2. 登录 platform.deepseek.com 查看余额\n"
    else:
        out += f"✅ API 连通正常\n"
        out += f"   检测模型: {result.get('model', '?')}\n\n"

    out += "─" * 60 + "\n"
    out += "📋 可用模型:\n"
    for key, info in MODEL_COSTS.items():
        out += f"  {info['emoji']} {info['label']}\n"
        out += f"     API ID: {MODEL_ID_MAP.get(key, '?')}\n"
        out += f"     成本: ¥{info['in']}/¥{info['out']} per 1M {info.get('note', '')}\n"
        strength_map = {
            "deepseek-v4-pro": "复杂推理 · CoT · 缠论 · 策略 · 多步逻辑",
            "deepseek-v4-flash": "日常问答 · 数据整理 · 快速响应 · 低价",
            "deepseek-r1": "超长CoT · 复杂数学 · 已被V4 Pro覆盖",
        }
        out += f"     擅长: {strength_map.get(key, '—')}\n\n"

    out += "─" * 60 + "\n"
    out += "🧠 自动路由规则:\n"
    out += "  → 本地计算: 日期/数学/汇率/简单行情（零成本）\n"
    out += "  → V4 Flash: 日常问答/数据整理/翻译（默认）\n"
    out += "  → V4 Pro:   缠论/策略/基本面/多步推理/风险评估\n"
    out += "  → R1:       超长CoT深度推理（force_model 强制指定）\n\n"

    out += "─" * 60 + "\n"
    out += "🔧 使用:\n"
    out += "  router_query(query)                                   → 自动路由（推荐）\n"
    out += "  router_query(query, force_model='deepseek-v4-pro')    → 强制 Pro\n"
    out += "  router_query(query, force_model='deepseek-v4-flash')  → 强制 Flash\n"
    out += "  router_query(query, force_model='deepseek-r1')        → 强制 R1\n"
    out += "  router_deepseek_status()                              → 本健康检查\n"
    return out


# ════════════════════════════════════════════════════════════════
# 🔌 Provider 工具集（合并自 deepcode-telemetry/provider_router.py）
# 原 provider-router MCP 服务器的 5 个工具，在此统一收编
# ════════════════════════════════════════════════════════════════

@mcp.tool()
async def router_provider_switch(name: str) -> str:
    """🔌 切换活跃 Provider — 热切换多 Provider 抽象层的当前生效 Provider。

    Args:
        name: Provider 名称，如 "deepseek" | "anthropic"（需已注册且 API Key 可用）

    Returns:
        切换结果
    """
    try:
        result = provider_router.switch(name)
        if not result.get("ok"):
            return f"❌ 切换失败: {result.get('error', '未知错误')}"
        cfg = provider_router.active_config
        return (
            f"✅ 已切换到 Provider: {result['active']}\n"
            f"  类型: {cfg.type.value} | 模型: {cfg.model} | API: {cfg.api_base}"
        )
    except Exception as e:  # noqa: BLE001
        return f"❌ 切换异常: {e}"


@mcp.tool()
async def router_provider_list() -> str:
    """🔌 列出所有已注册 Provider — 含类型、模型、活跃标记、API 地址。

    Returns:
        Provider 列表
    """
    providers = provider_router.list_providers()
    if not providers:
        return "📭 暂无已注册 Provider"
    lines = ["🔌 已注册 Provider:"]
    for p in providers:
        marker = "◀ 活跃" if p["active"] else ""
        lines.append(
            f"  - {p['name']} [{p['type']}]  model={p['model']}  {marker}\n"
            f"    api_base: {p['api_base']}"
        )
    lines.append("\n切换: router_provider_switch(name)")
    return "\n".join(lines)


@mcp.tool()
async def router_provider_status() -> str:
    """🔌 Provider 抽象层状态 — 当前活跃 Provider + 全部注册 Provider。

    Returns:
        状态报告
    """
    st = provider_router.status()
    lines = [f"🔌 Provider 抽象层状态\n当前活跃: {st['active']}"]
    for p in st["providers"]:
        marker = "◀ 活跃" if p["active"] else ""
        lines.append(f"  - {p['name']} [{p['type']}]  model={p['model']}  {marker}")
    return "\n".join(lines)


@mcp.tool()
async def router_provider_chat(
    messages: list,
    model: str = "",
    max_tokens: int = 0,
) -> str:
    """🔌 使用活跃 Provider 发送 LLM 请求 — 直连当前 Provider，不经过自动路由。

    适合明确指定 Provider 直聊的场景（如 Anthropic 对比测试）。

    Args:
        messages: OpenAI 格式消息数组，如 [{"role": "user", "content": "你好"}]
        model:    覆盖模型名（可选，默认用 Provider 配置的模型）
        max_tokens: 最大输出 Token（0=用 Provider 配置的默认值）

    Returns:
        LLM 回复 + 用量 + 延迟
    """
    if not messages:
        return "❌ messages 不能为空"
    resp = provider_router.chat(messages, model=model, max_tokens=max_tokens)
    if not resp.ok:
        return f"❌ Provider 请求失败: {resp.error}"
    out = [f"🤖 {resp.model} 回复:"]
    out.append(resp.content)
    out.append(f"\n──\n⚡ 延迟 {resp.latency_ms}ms | 用量: {json.dumps(resp.usage, ensure_ascii=False)}")
    return "\n".join(out)


@mcp.tool()
async def router_provider_recommend(effort: str = "high") -> str:
    """🔌 根据 effort 推荐模型 — 统一事实来源 EFFORT_MODEL_MAP。

    Args:
        effort: 思考深度: "low" | "medium" | "high" | "xhigh" | "max"（默认 high）

    Returns:
        推荐模型 + max_tokens + 成本
    """
    rec = effort_to_model(effort)
    return (
        f"🎚️ effort={rec['effort']} → 推荐 {rec['emoji']} {rec['label']}\n"
        f"  模型: {rec['model']} (API: {rec['model_id']})\n"
        f"  max_tokens: {rec['max_tokens']}\n"
        f"  成本: ¥{rec['cost_in']}/百万输入 · ¥{rec['cost_out']}/百万输出\n"
        f"  说明: {rec['note']}"
    )


# ════════════════════════════════════════════════════════════════
# 🪜 分层策略工具集（纯付费 · 自动降级）
# ════════════════════════════════════════════════════════════════

# ── 缓存热身前缀 (2026-08-19, DSH 前缀稳定性第4层移植; 2026-08-20 提取为单一真相源) ──
# 定义在 cache_prewarm.py (平级模块, 与 router_cascade 同款导入惯例)。router 与
# compact_governance.structured_summarize 共享同一前缀 → 全局缓存前缀不分叉。
# DeepSeek 前缀缓存块粒度 ~2048 tokens。短查询(system ~20 tokens)永远够不到门槛
# → cache_hit=0 → 缓存省钱失效。方案: 固定、静态、业务无关的长前缀注入 system,
# 使每次请求达到缓存块粒度。前缀内容永不变化 → 全局会话共享同一缓存前缀。
# ⚠️ 这是"热身"块: 永远不要在此块内放任何会随请求变化的内容, 否则缓存前缀分叉。
import cache_prewarm  # noqa: E402 — 平级导入, 与 router_cascade 同惯例

CACHE_PREWARM_PREFIX = cache_prewarm.CACHE_PREWARM_PREFIX

TIER_DEFAULT_MAX_TOKENS = {"grunt": 2048, "code": 8192, "main": 4096, "deep": 8192, "local": 4096}

# 通道冷却注册表: key = f"{tier}::{provider}::{model}" → 允许重试的 epoch 秒
# 429 带 Retry-After 时记录，期间直接跳过，避免每次浪费一次无效请求
_channel_cooldown: dict[str, float] = {}


def _channel_in_cooldown(key: str) -> float:
    """返回剩余冷却秒数（0 = 不在冷却）。"""
    until = _channel_cooldown.get(key, 0.0)
    remain = until - datetime.now().timestamp()
    return remain if remain > 0 else 0.0


def _tier_channel_key(tier: str, ch: dict) -> str:
    return f"{tier}::{ch['provider']}::{ch['model']}"


def _tier_record_channel(tier: str, ch: dict, ok: bool) -> None:
    key = _tier_channel_key(tier, ch)
    rec = _tier_stats["per_channel"].setdefault(key, {"ok": 0, "fail": 0})
    rec["ok" if ok else "fail"] += 1


def _estimate_deepseek_cost(model_key: str, usage: dict) -> float:
    """按 DeepSeek 官方牌价 + 前缀缓存命中价估算成本（¥）。"""
    costs = MODEL_COSTS.get(model_key, {"in": 1, "out": 2})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    cache_hit = usage.get("prompt_cache_hit_tokens", 0)
    cache_miss = max(prompt_tokens - cache_hit, 0)
    cache_price = costs.get("cache_hit", costs["in"] * 0.1)
    return (cache_miss * costs["in"] + cache_hit * cache_price
            + completion_tokens * costs["out"]) / 1_000_000


async def _tier_dispatch(
    query: str,
    tier: str = "auto",
    system: str = "",
    max_tokens: int = 0,
    temperature: float = 0.4,
) -> dict:
    """分层分发核心：按 tier 顺序尝试通道，失败/缺Key/限流自动降级。

    Returns: {"ok": bool, "tier", "reason", "channel", "content",
              "cost_yuan", "latency_ms", "usage", "fallbacks", "detail"}
    """
    # ── 解析层 ──
    if tier in TIER_POLICY:
        tier_name = tier
        reason = "用户指定层"
    elif tier in ("auto", ""):
        cls = classify_tier(query)
        tier_name = cls["tier"]
        reason = cls["reason"]
    else:
        tier_name = "main"
        reason = f"未知层 '{tier}'，回退主力层"

    policy = TIER_POLICY[tier_name]

    # ── 缓存热身前缀注入 (2026-08-19) ──
    # 固定静态前缀拼在 system 最前面, 让短查询也达到 DeepSeek 2048-token 缓存门槛。
    # 若调用方传了自定义 system, 前缀仍加在最前(前缀必须固定, 追加内容不影响命中)。
    _system = system or ""

    # code 层默认 system prompt：调用方未指定时注入 DEEPCODE 编程环境提示词，
    # 让 V4 Flash 明确任务边界，减少结构漂移
    if tier_name == "code" and not _system:
        _system = (
            "你是 DEEPCODE 的编程引擎，负责生成高质量代码。\n"
            "规则：\n"
            "1. 只输出代码和必要说明，不要闲聊。\n"
            "2. 复杂嵌套结构先输出完整骨架（带注释占位），再逐层填充；禁止先建空壳再补内容。\n"
            "3. 一次性输出完整结构，不要先输出顶层再回头修改；修结构用整体重写，不打补丁。\n"
            "4. 嵌套超过 3 层拆成中间变量或辅助函数。\n"
            "5. 保证括号/缩进/层级完全闭合后再输出；输出前自检一遍。\n"
            "6. 代码要可直接运行：补全 import、处理边界条件、避免空实现。"
        )

    if _system:
        system = CACHE_PREWARM_PREFIX + _system
    else:
        system = CACHE_PREWARM_PREFIX

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": query})
    max_tokens = max_tokens or TIER_DEFAULT_MAX_TOKENS[tier_name]

    fallbacks = 0
    tried = []
    channel_errors = []
    for ch in policy["channels"]:
        # 缺 Key → Provider 未注册 → 跳过
        if ch["provider"] not in provider_router.providers:
            tried.append(f"{ch['provider']}/{ch['model']}(无Key)")
            continue
        key = _tier_channel_key(tier_name, ch)
        # 429 冷却中 → 跳过
        cd = _channel_in_cooldown(key)
        if cd > 0:
            tried.append(f"{ch['provider']}/{ch['model']}(限流冷却{int(cd)}s)")
            continue
        tried.append(f"{ch['provider']}/{ch['model']}")
        resp = await asyncio.to_thread(
            provider_router.chat_via, ch["provider"], messages, ch["model"],
            max_tokens, temperature
        )
        if resp.ok:
            cost = (
                _estimate_deepseek_cost(ch["model"], resp.usage)
                if ch["provider"] == "deepseek"
                else 0.0
            )
            _tier_record_channel(tier_name, ch, ok=True)
            if fallbacks > 0:
                _tier_stats["fallbacks"] += 1
            return {
                "ok": True,
                "tier": tier_name,
                "reason": reason,
                "channel": ch,
                "content": resp.content,
                "cost_yuan": cost,
                "latency_ms": resp.latency_ms,
                "usage": resp.usage,
                "fallbacks": fallbacks,
                "tried": tried,
                "channel_errors": channel_errors,
                "detail": "",
            }
        _tier_record_channel(tier_name, ch, ok=False)
        # 429 限流 → 记录冷却，避免窗口期内反复无效尝试
        if resp.retry_after > 0:
            _channel_cooldown[key] = datetime.now().timestamp() + resp.retry_after
            channel_errors.append(
                f"{ch['provider']}/{ch['model']}: 限流 {resp.error[:100]}"
                f"（冷却 {resp.retry_after}s）"
            )
        else:
            channel_errors.append(f"{ch['provider']}/{ch['model']}: {resp.error[:120]}")
        fallbacks += 1

    return {
        "ok": False,
        "tier": tier_name,
        "reason": reason,
        "channel": None,
        "content": "",
        "cost_yuan": 0.0,
        "latency_ms": 0,
        "usage": {},
        "fallbacks": fallbacks,
        "tried": tried,
        "channel_errors": channel_errors,
        "detail": f"所有通道均失败，尝试过: {', '.join(tried) or '（无可用通道，请检查 .env 中 DEEPSEEK_API_KEY 或 SILICONFLOW_API_KEY）'}",
    }


def _format_tier_output(result: dict) -> str:
    tier = result["tier"]
    policy = TIER_POLICY[tier]
    if not result["ok"]:
        return (
            f"❌ [{policy['emoji']} {policy['label']} · 分层路由]\n"
            f"🔄 层: {tier}（{result['reason']}）\n"
            f"⚠️ {result['detail']}"
        )
    ch = result["channel"]
    emoji = policy["emoji"]
    cost_flag = "本地" if ch["cost"] == 0 else f"¥{ch['cost']}/¥{ch.get('cost_out', ch['cost'] * 2)}"
    out = f"【{emoji} {policy['label']} · 分层路由】\n"
    out += f"🔄 层: {tier} → {result['reason']}\n"
    out += f"✅ 通道: {ch['provider']}/{ch['model']}（{ch.get('note', '')}）\n"
    if result["fallbacks"] > 0:
        out += f"⬇️ 已降级 {result['fallbacks']} 次: {' → '.join(result['tried'])}\n"
        errs = result.get("channel_errors") or []
        if errs:
            out += "  失败原因:\n"
            for e in errs:
                out += f"    ❌ {e}\n"
    out += f"\n{result['content']}\n\n"
    out += "─" * 50 + "\n"
    usage = result.get("usage", {})
    out += f"📊 模型: {ch['provider']}/{ch['model']}\n"
    out += f"📊 Token: 入{usage.get('prompt_tokens', 0)} + 出{usage.get('completion_tokens', 0)}"
    out += f" = {usage.get('total_tokens', 0)} | 延迟: {result['latency_ms']}ms\n"
    out += f"💰 成本: ¥{result['cost_yuan']:.4f}（{cost_flag}）"
    return out


@mcp.tool()
async def router_tier_query(
    query: str,
    tier: str = "auto",
    system: str = "",
    max_tokens: int = 0,
    temperature: float = 0.4,
) -> str:
    """
    🪜 分层路由查询 — 纯付费策略：按内容分层选模型，失败自动降级兜底。

    五层策略:
    - grunt 打杂 🧹: 翻译/格式化/抽取/摘要 → V4 Flash(¥1)
    - code  代码活 🔧: 代码审查/PoC/模式匹配 → V4 Flash(¥1)
    - main  主力   ⚡: 复杂分析/多轮迭代 → V4 Flash(¥1)
    - deep  攻坚   🔴: 利用链/深度推理/报告评审 → V4 Pro(¥3)
    - local 本地   🖥️: NDA/敏感任务(数据不出网) → Ollama Qwen2.5-3B(本机11434,快且隐私)

    Args:
        query:      任务内容
        tier:       强制指定层: "auto"(默认,按内容分类) | "grunt" | "code" | "main" | "deep" | "local"
        system:     可选系统提示词
        max_tokens: 最大输出 Token（0=按层默认）
        temperature: 温度 (0.0-1.0，默认 0.4，官方 0.7 与低采样防结构漂移的折中)

    Returns:
        处理结果 + 命中通道 + 成本 + 降级记录
    """
    _tier_stats["total"] += 1
    result = await _tier_dispatch(query, tier=tier, system=system,
                                  max_tokens=max_tokens, temperature=temperature)

    # 统计累计
    per = _tier_stats["per_tier"].setdefault(result["tier"], {"calls": 0, "cost_yuan": 0.0})
    per["calls"] += 1
    per["cost_yuan"] += result["cost_yuan"]
    _tier_stats["cost_yuan"] += result["cost_yuan"]
    if result["cost_yuan"] == 0 and result["ok"]:
        _tier_stats["free_calls"] += 1
    elif result["ok"]:
        _tier_stats["paid_calls"] += 1
    _tier_stats["last"] = {"tier": result["tier"], "query": query[:80]}

    return _format_tier_output(result)


@mcp.tool()
async def router_tier_status() -> str:
    """
    🪜 分层策略状态面板 — 查看 4 层策略、通道可用性、统计与降级情况。

    Returns:
        分层策略完整状态
    """
    out = "═" * 60 + "\n"
    out += "🪜 Router MCP · 分层策略（纯付费 · 已移除免费外部模型）\n"
    out += "═" * 60 + "\n\n"

    # 通道可用性
    out += "🔑 Provider Key 状态:\n"
    key_state = {
        "deepseek": bool(API_KEY),
        "zhipu": bool(ZHIPU_API_KEY),
        "siliconflow": bool(SILICONFLOW_API_KEY),
        "local": True,  # 本机 Ollama，常驻；11434 在线
    }
    for name, ok in key_state.items():
        out += f"  {'✅' if ok else '❌'} {name:<12} {'已配置' if ok else '未配置（对应 Provider 自动跳过）'}\n"

    out += "\n🧪 实验性通道: 已移除（所有免费外部 API 模型已于 2026-08-21 清除）\n\n"

    # 各层通道链
    out += "─" * 60 + "\n"
    out += "📋 分层策略:\n"
    for t, p in TIER_POLICY.items():
        out += f"\n  {p['emoji']} {t} · {p['label']}\n"
        for ch in p["channels"]:
            avail = "✅" if ch["provider"] in provider_router.providers else "❌"
            cost_txt = "免费(本地)" if ch["cost"] == 0 else f"¥{ch['cost']}"
            out += f"    {avail} {ch['provider']:<11} {ch['model']:<42} {cost_txt}\n"
            out += f"       ↳ {ch.get('note', '')}\n"

    # 运行统计
    out += "\n" + "─" * 60 + "\n"
    out += "📊 分层运行统计:\n"
    out += f"  总调用: {_tier_stats['total']} | 免费: {_tier_stats['free_calls']} | 付费: {_tier_stats['paid_calls']} | 降级: {_tier_stats['fallbacks']}\n"
    out += f"  总成本: ¥{_tier_stats['cost_yuan']:.4f}\n"
    for t, st in _tier_stats["per_tier"].items():
        emoji = TIER_POLICY.get(t, {}).get("emoji", "❓")
        out += f"    {emoji} {t}: {st['calls']} 次 · ¥{st['cost_yuan']:.4f}\n"
    channels = _tier_stats["per_channel"]
    if channels:
        out += "  通道明细:\n"
        for key, rec in sorted(channels.items()):
            out += f"    {key}: ✅{rec['ok']} ❌{rec['fail']}\n"

    # 冷却中的通道
    now = datetime.now().timestamp()
    cooling = {k: v - now for k, v in _channel_cooldown.items() if v - now > 0}
    if cooling:
        out += "\n  🧊 限流冷却中:\n"
        for key, remain in sorted(cooling.items()):
            out += f"    {key}: 剩余 {int(remain)}s\n"
    if _tier_stats["last"]:
        out += f"\n  最近: [{_tier_stats['last']['tier']}] \"{_tier_stats['last']['query']}\"\n"

    out += "\n🔧 使用:\n"
    out += "  router_tier_query(query)                    → 自动分层（推荐）\n"
    out += "  router_tier_query(query, tier='grunt')      → 强制打杂层\n"
    out += "  router_tier_query(query, tier='code')       → 强制代码层\n"
    out += "  router_tier_query(query, tier='deep')       → 强制攻坚层\n"
    out += "  router_tier_status()                        → 本状态面板\n"
    return out


# ════════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run()
