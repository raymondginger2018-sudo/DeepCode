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
| Provider 路由 | `core/mcp_servers/router_mcp_server.py` | aws-smithy-runtime + provider.active |

## 遥测 (telemetry.py)

- **Trace Spans**: 工具调用 / LLM 调用 / 会话生命周期
- **Metrics**: 耗时 / Token 用量 / 错误率 / 计数器
- **导出**: JSON File / OTLP HTTP / Prometheus / Console
- **环境变量**: OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_METRIC_EXPORT_INTERVAL

## Provider 路由 (router-mcp)

provider-router 已整合进 router-mcp（2026-08-12），由 `core/mcp_servers/router_mcp_server.py` 统一提供。

- **Provider**: deepseek（始终注册）/ anthropic（存在 `ANTHROPIC_API_KEY` 环境变量时自动注册）
- **Effort 路由**: low→flash, high→pro, xhigh→r1, max→r1（单一事实来源 `EFFORT_MODEL_MAP`）
- **MCP 工具**: `router_provider_switch` / `router_provider_list` / `router_provider_status` / `router_provider_chat` / `router_provider_recommend`

## CLI

```bash
python telemetry.py demo
```

Provider 相关操作请使用 router-mcp 的 `router_provider_*` 工具，例如：
`router_provider_recommend(effort='xhigh')` / `router_provider_list()` / `router_provider_switch('anthropic')`

## MCP Server

```json
"deepcode-telemetry": {
  "command": "python",
  "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-telemetry/telemetry.py", "--mcp"]
}
```

> 注: `deepcode-providers` 独立 MCP 已移除（2026-08-12 整合进 router-mcp）
