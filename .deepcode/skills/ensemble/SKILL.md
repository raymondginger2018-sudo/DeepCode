---
name: ensemble
description: >
  多模型集成分析 A 股标的。当用户提到 ensemble、集成分析、多模型、模型投票、
  交叉验证、或者用 /ensemble 命令时触发。也应在用户要求"全面分析"某只股票、
  "用所有模型跑一下"、"深度分析"时主动触发。默认三模型快速模式(DeepSeek V4 Pro +
  GLM-5.2 + Qwen3.5 122B)，--full 升级到六模型全景模式(外加 MiniMax M3 +
  Mistral Large 675B + StockMark 100B)。全部走 NVIDIA NIM 免费 API。
---

# 多模型集成分析

对 A 股标的运行多模型集成投票分析。全部模型通过 NVIDIA NIM 免费 API 调用。

## 用法

```
/ensemble <股票代码> [股票名称]    →  三模型快速集成 (默认, ~10秒)
/ensemble <代码> --full             →  六模型全景集成 (~30秒)
/ensemble <代码1> <代码2>          →  双股对比
```

## 执行

根据用户输入构造命令行：

**三模型 (默认)**:
```bash
python quant_trading/llm_reasoner.py --code <代码> --name <名称> --mode ensemble
```

**六模型 (--full)**:
```bash
python quant_trading/llm_reasoner.py --code <代码> --name <名称> --mode ensemble4
```

**双股对比**: 分别对每只股票运行三模型分析。

## 输出格式

从命令输出中提取关键信息，用以下格式呈现：

```
## 🔬 多模型集成分析 — <名称>(<代码>)

| 模型 | 判断 | 评分 | 状态 |
|------|------|------|------|
| DeepSeek V4 Pro | BUY | 6.5 | ✅ |
| GLM-5.2         | BUY | 6.5 | ✅ |
| Qwen3.5 122B    | HOLD | 5.0 | ✅ |

### 投票结果
- 票数: <B>B/<H>H/<S>S
- 多数意见: BUY/HOLD/SELL
- 置信度: HIGH+ / HIGH / MEDIUM / LOW
- 建议仓位: X.X%

### 分歧备注 (如有)
> ...

### 关键价位
- 支撑: xx.xx / xx.xx
- 阻力: xx.xx / xx.xx
```

## 模型阵容

| # | 模型 | 参数 | 角色 | 特长 |
|---|------|------|------|------|
| A | DeepSeek V4 Pro | 685B | 主审 | A股分析，中文推理 |
| B | GLM-5.2 | 753B | 复核 | 数学推理，逻辑 |
| C | Qwen3.5 122B | 122B | 研究 | 财报解析，快速 |
| D | MiniMax M3 | ~500B | 研究 | 中文原生深度 |
| E | Mistral Large | 675B | 极速 | 1.2s，逻辑严密 |
| F | StockMark 100B | 100B | 金融 | 金融专用模型 |

全部走 NVIDIA NIM 免费 API，零成本。

## 注意事项

- 必须先运行 `python quant_trader.py prewarm` 确保数据缓存是最新的
- 盘中分析建议三模型 (快)，盘后可用六模型 (全面)
- 股票代码必须是 6 位数字 (如 `002049`)
- 如果模型不可用，自动降级到可用模型投票
- 如果用户只说"分析XXX"而未指定 ensemble，优先使用 `chanlun-analysis` skill 而非本 skill；本 skill 仅在用户明确要求多模型集成时触发
