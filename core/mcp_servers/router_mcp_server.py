# -*- coding: utf-8 -*-
"""
█▀█ █░█ █▀█ █▄▄ █▀█ █▀▀
█▀▄ █▄█ █▀▀ █▄█ █▄█ ██▄

Router MCP · 通用智能路由引擎
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  轻量问答   → V4 Flash   (¥1)   ⚡
  复杂分析   → V4 Pro     (¥3)   🧠
  缠论推演   → V4 Pro     (¥3)   📐
  日常数据   → 本地计算   (¥0)   💻

类似 deepseek-direct 自动路由，扩展到全系统。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os
import re
import json
import math
import asyncio
import traceback
from datetime import datetime, date, timedelta
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

DEEPSEEK_BASE = "https://api.deepseek.com/v1"
API_KEY = os.environ.get("DEEPSEEK_API_KEY")
if not API_KEY:
    raise RuntimeError("DEEPSEEK_API_KEY 未设置。请创建 .env 文件并添加 DEEPSEEK_API_KEY=your_key")

# 成本表（¥/百万 tokens）— 与 deepseek-direct 合并后统一口径
# cache_hit 为 DeepSeek 前缀缓存命中价（逆向恢复官方 CNY 牌价，v4-flash 1:50:100、v4-pro 1:120:240）
MODEL_COSTS = {
    "deepseek-v4-flash": {"in": 1,  "out": 2,  "cache_hit": 0.02,  "emoji": "⚡", "label": "V4 Flash", "note": "官方牌价 ¥1/¥2，缓存命中 ¥0.02"},
    "deepseek-v4-pro":   {"in": 3,  "out": 6,  "cache_hit": 0.025, "emoji": "🧠", "label": "V4 Pro",   "note": "官方牌价 ¥3/¥6，缓存命中 ¥0.025"},
    "deepseek-r1":       {"in": 4,  "out": 16, "emoji": "🔴", "label": "R1 推理",  "note": "（推荐用 V4 Pro 替代，缓存价未确认按 1/10 近似）"},
}

# 模型 ID 映射（API 实际使用的模型名）
MODEL_ID_MAP = {
    "deepseek-v4-pro":   "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek-r1":       "deepseek-reasoner",
}

# ── 运行时统计 ──
_stats = {
    "total_queries": 0,
    "routes": {"simple": 0, "complex": 0, "chan_theory": 0, "data_query": 0},
    "total_cost_yuan": 0.0,
    "prompt_cache_hit_tokens": 0,    # DeepSeek 前缀缓存命中 tokens（免费羊毛）
    "prompt_cache_miss_tokens": 0,   # 前缀缓存未命中 tokens
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
    """调用 DeepSeek API (OpenAI-compatible)"""
    model_id = MODEL_ID_MAP.get(model_key, model_key)

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=180) as client:
        try:
            resp = await client.post(
                f"{DEEPSEEK_BASE}/chat/completions",
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

        return {
            "content": content,
            "reasoning": reasoning,
            "model": data.get("model", model_id),
            "usage": usage,
            "cost_yuan": cost,
            "cache_hit_tokens": cache_hit,
            "cache_miss_tokens": cache_miss,
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
        out += f"⚡ 前缀缓存: 命中 {hit_t} / 未命中 {miss_t} tokens (命中率 {hit_rate:.1f}%)\n"
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
                     优先级高于 force_route 和自动路由

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
    hit_tokens = _stats.get("prompt_cache_hit_tokens", 0)
    miss_tokens = _stats.get("prompt_cache_miss_tokens", 0)
    total_prompt = hit_tokens + miss_tokens
    if total_prompt > 0:
        hit_rate = hit_tokens / total_prompt * 100
        out += f"  ⚡ DeepSeek 前缀缓存: 命中 {hit_tokens} / 未命中 {miss_tokens} tokens (命中率 {hit_rate:.1f}%)\n"
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

    out += "\n" + "─" * 60 + "\n"
    out += "🔧 使用:\n"
    out += "  router_query(query)                              → 自动路由（推荐）\n"
    out += "  router_query(query, force_route='complex')       → 强制复杂分析\n"
    out += "  router_query(query, force_route='data_query')    → 强制本地计算\n"
    out += "  router_query(query, force_model='deepseek-v4-pro')   → 强制 V4 Pro\n"
    out += "  router_query(query, force_model='deepseek-r1')       → 强制 R1 推理\n"
    out += "  router_deepseek_status()                         → API 健康检查\n"
    out += "  router_analyze_breakdown()                       → 成本分析\n"

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
# 入口
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run()
