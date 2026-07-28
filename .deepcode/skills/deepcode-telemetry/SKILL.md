---
name: deepcode-telemetry
description: >
  DeepCode Telemetry — 移植自 CODEX.EXE 的 OpenTelemetry 遥测栈。
  Trace Spans, Metrics, OTLP/Prometheus/JSON 导出, 多 Provider 路由。
  对标 codex.exe: opentelemetry-otlp-0.31.0, SessionTelemetry, model_preferences.
version: 1.0.0
author: DeepCode + RE (codex.exe v0.145.0)
date: 2026-07-28
tags: [telemetry, otel, metrics, provider, routing]
---

# DeepCode Telemetry

移植自 **CODEX.EXE** 的完整遥测 + Provider 抽象栈。

## 模块

| 模块 | 文件 | 对标 CODEX |
|:------|:-----|:----------|
| Trace/Metrics | `telemetry.py` | opentelemetry-otlp-0.31.0 |
| Provider 路由 | `provider_router.py` | aws-smithy-runtime + provider.active |

## 遥测 (telemetry.py)

- **Trace Spans**: 工具调用 / LLM 调用 / 会话生命周期
- **Metrics**: 耗时 / Token 用量 / 错误率 / 计数器
- **导出**: JSON File / OTLP HTTP / Prometheus / Console
- **环境变量**: OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_METRIC_EXPORT_INTERVAL

## Provider 路由 (provider_router.py)

- **5 种 Provider**: OpenAI / Anthropic / DeepSeek / Bedrock / Vertex
- **Effort 路由**: low→flash, high→pro, xhigh→r1, max→r1
- **热切换**: `provider_router.py switch anthropic`

## CLI

```bash
python telemetry.py demo
python provider_router.py list
```

## MCP Server

```json
"deepcode-telemetry": {
  "command": "python",
  "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-telemetry/telemetry.py", "--mcp"]
},
"deepcode-providers": {
  "command": "python",
  "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-telemetry/provider_router.py", "--mcp"]
}
```
