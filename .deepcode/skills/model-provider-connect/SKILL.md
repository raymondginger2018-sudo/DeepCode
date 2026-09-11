---
name: model-provider-connect
description: 把 SCNET（国家超算互联网 Token Plan）和硅基流动 SiliconFlow 的模型联通到 DEEPCODE 的 /MODEL 下拉菜单。覆盖 .env 密钥配置、router_mcp_server.py 的 provider 注册、cli.js 菜单项、settings.json provider 块、连通性验证与全部踩坑点（模型 ID 大小写敏感、max_tokens 方言、SSL EOF、套餐外 403、余额 30001 等）。Use when 用户提到 SCNET、硅基流动、SiliconFlow、/MODEL 菜单加模型、模型连不上、套餐密钥 sk-tp- 等。
---

# Model Provider Connect — SCNET & 硅基流动 联通指南

> 本 Skill 记录 2026-08-21~23 实测踩坑后沉淀的**完整接入链路**，目标是让「再接一次」不再痛苦。
> 关联记忆：`scnet-token-plan-setup` / `scnet-model-id-and-max-tokens-pitfalls` / `scnet-token-plan-available-models-2026-08-23` / `ds-v4flash-three-endpoints` / `task-model-free-models-20260821`

## 一句话总结

接入 = 3 步：**① .env 加密钥 → ② router_mcp_server.py 注册 provider → ③ /MODEL 菜单入口（cli.js + settings.json）**。所有失败几乎都出在「模型 ID 大小写」和「参数方言」上。

---

## 1. 两个 Provider 的端点速查（勿再踩）

| 项目 | SCNET 国家超算互联网 | 硅基流动 SiliconFlow |
|---|---|---|
| Base URL (OpenAI 兼容) | `https://api.scnet.cn/api/llm/v1` | `https://api.siliconflow.cn/v1` |
| Base URL (Anthropic 兼容) | `https://api.scnet.cn/api/llm/anthropic` | 同 OpenAI 端点（默认不单独提供） |
| 密钥环境变量 | `SCNET_TP_API_KEY`（`sk-tp-` 前缀 = Token Plan 套餐） | `SILICONFLOW_API_KEY` |
| 密钥前缀含义 | `sk-tp-`=Token Plan 套餐 / `sk-`=按量计费 / `sk-sp-`=Coding Plan，**三套互不通用** | 统一 `sk-` 前缀 |
| 模型 ID 风格 | **裸名 + 大小写严格**：`GLM-5.2`、`DeepSeek-V4-Flash` | **供应商前缀**：`deepseek-ai/DeepSeek-V4-Flash`、`zai-org/GLM-5.2` |
| 计费 | 套餐额度抵扣（按月清零，用尽直接报错不转按量） | 按量 USD 计费 |

## 2. 三步接入链路（详细）

### 第 1 步：.env 加密钥

在 `F:/DEEPCODE/.env` 添加：

```bash
SCNET_TP_API_KEY=sk-tp-xxx          # SCNET Token Plan 套餐密钥
SILICONFLOW_API_KEY=sk-xxx          # 硅基流动密钥
```

> ⚠️ 千万别拿按量 `sk-` 密钥当套餐密钥用：SCNET 会按量计费或报错，套餐额度不抵扣。
> ⚠️ 改 .env 后必须**重启 deepcode 进程**才生效（router server 启动时读一次）。

### 第 2 步：router_mcp_server.py 注册 provider

文件：`F:/DEEPCODE/core/mcp_servers/router_mcp_server.py`

**②a. 密钥加载常量区**（文件顶部约 100-108 行）：

```python
SILICONFLOW_API_KEY = _load_env_key("SILICONFLOW_API_KEY")
SCNET_TP_API_KEY = _load_env_key("SCNET_TP_API_KEY")

SILICONFLOW_BASE = "https://api.siliconflow.cn/v1"
SCNET_BASE = "https://api.scnet.cn/api/llm/v1"
```

**②b. Provider 注册块**（`_setup_providers` 方法内，约 460-498 行），按需加入：

```python
# 硅基流动（余额不足时 API 返回 30001，分层路由会自动降级官方通道）
if SILICONFLOW_API_KEY:
    self.register(ProviderConfig(
        name="siliconflow-dsv4flash", type=ProviderType.OPENAI,
        api_base=SILICONFLOW_BASE, api_key=SILICONFLOW_API_KEY,
        model="deepseek-ai/DeepSeek-V4-Flash", timeout=90,
    ))
    self.register(ProviderConfig(
        name="siliconflow-glm52", type=ProviderType.OPENAI,
        api_base=SILICONFLOW_BASE, api_key=SILICONFLOW_API_KEY,
        model="zai-org/GLM-5.2", timeout=180,   # 推理型冷启动慢，放宽 timeout
    ))

# SCNET Token Plan 套餐
if SCNET_TP_API_KEY:
    self.register(ProviderConfig(
        name="scnet", type=ProviderType.OPENAI,
        api_base=SCNET_BASE, api_key=SCNET_TP_API_KEY,
        model="GLM-5.2", timeout=180,
    ))
```

> ⚠️ **模型 ID 必须逐一核对**：SCNET 大小写敏感且必须与 `/models` 返回完全一致（小写 `glm-5.2` → 422 Model Not Exist，`glm-5-2` 也不对）；硅基必须有 `zai-org/`、`deepseek-ai/` 这类组织前缀。

### 第 3 步：/MODEL 下拉菜单入口

**③a. cli.js 菜单项**：`F:/DEEPCODE/.deepcode/cli/cli.js` 中搜索 `/MODEL` 菜单数组（此前备份：`cli.js.bak-20260821-sf-v32`、`cli.js.bak-20260823-sc-v4pro` 可对照），追加菜单项：

```js
// 示例菜单项结构（与现有项保持一致）
{ label: "SCNet · GLM-5.2",       value: "scnet",           provider: "scnet" },
{ label: "硅基流动 · V4 Flash",    value: "siliconflow-dsv4flash", provider: "siliconflow-dsv4flash" },
```

**③b. settings.json provider 块**（`.deepcode/settings.json` → `provider.providers`），声明供 CLI 框架识别：

```json
"scnet": { "type": "openai", "apiBase": "https://api.scnet.cn/api/llm/v1",
           "apiKey": "${SCNET_TP_API_KEY}", "models": ["GLM-5.2", "DeepSeek-V4-Flash"] },
"siliconflow": { "type": "openai", "apiBase": "https://api.siliconflow.cn/v1",
           "apiKey": "${SILICONFLOW_API_KEY}", "models": ["deepseek-ai/DeepSeek-V4-Flash", "zai-org/GLM-5.2"] }
```

**③c. 重启生效**：`deepcode` 重开（或重启 router-mcp server）。改 cli.js 后不重启，菜单不刷新。

## 3. 连通性验证

```bash
# 直接 curl 测端点（不经过 DEEPCODE，先定位是 API 问题还是集成问题）
curl -s https://api.scnet.cn/api/llm/v1/models \
  -H "Authorization: Bearer $SCNET_TP_API_KEY" | jq '.data[]?.id' | head -20

curl -s https://api.siliconflow.cn/v1/models \
  -H "Authorization: Bearer $SILICONFLOW_API_KEY" | jq '.data[]?.id' | grep -i -E "v4-flash|glm-5" 

# 真实补全调用（SCNET 裸名大小写严格）
curl -s https://api.scnet.cn/api/llm/v1/chat/completions \
  -H "Authorization: Bearer $SCNET_TP_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"GLM-5.2","messages":[{"role":"user","content":"ping"}],"max_tokens":256}'
```

集成侧验证：MCP 工具 `router_status`（查看已注册 provider 与 active 状态）、`router_deepseek_status`（API 健康）。

## 4. 踩坑清单（血泪史，务必逐条对照）

### SCNET 专属坑
| # | 症状 | 原因 | 解法 |
|---|---|---|---|
| 1 | `422 Model Not Exist` | 模型 ID 大小写敏感，必须与 /models 返回**完全一致** | 严格用 `GLM-5.2` / `DeepSeek-V4-Flash` 裸名；小写/连字符变体全挂 |
| 2 | choices 为空 / 输出被截断 | GLM-5.2 是推理型模型，max_tokens 被 reasoning_content 吃掉 | 直调至少给 max_tokens≥256；DEEPCODE 分层 deep 层默认 ≥8192 无碍 |
| 3 | `SSL UNEXPECTED_EOF_WHILE_READING` | SCNet 网关不稳定（curl 正常、urllib 偶发） | 重试即可；分层路由的降级机制能兜住 |
| 4 | `max_completion_tokens`/`thinking` 参数不稳定 | SCNet 只稳定吃 `max_tokens`+采样字段 | DS 通道只用 max_tokens；与 Kimi-K3（恰好相反，只吃 max_completion_tokens）区分 |
| 5 | `403 model does not support Token Plan` | 模型不在套餐抵扣表（如 V3.2、MiMo-V2.5-Pro 实测 403） | 对照套餐模型表（见第 5 节）选模型 |
| 6 | 额度用尽直接报错 | Token Plan 不转按量、按月清零 | 及时关注套餐余额，别拿它跑高消耗任务 |
| 7 | 旧套餐密钥调不通 | `sk-tp-`/`sk-`/`sk-sp-` 三套互不通用 | 确认密钥前缀与套餐类型匹配 |

### 硅基流动专属坑
| # | 症状 | 原因 | 解法 |
|---|---|---|---|
| 1 | `30001` 错误 | 账户余额不足 | 充值或走官方/SCNET 通道；router 会自动降级 |
| 2 | 模型 ID 缺组织前缀返回 404/无效 | 需 `deepseek-ai/`、`zai-org/` 前缀 | 从 /models 响应里复制完整 id |
| 3 | 冷启动 10s+ | Serverless 首次拉起慢 | timeout 放宽到 90-180s，别误判超时 |
| 4 | `reasoning_effort=off` | 关不掉思考（返回 200 但思考仍开） | 接受默认思考，或换官方端点 |

### 通用坑
- 改 .env / cli.js / settings.json 后**不重启不生效** — 最常见「明明配了为什么没有」的原因。
- SCNET 模型 ID 与硅基**不能互拷**：SCNET 用裸名、硅基用带前缀全名。

## 5. 常用模型 ID 参考表（2026-08-23 实测）

### SCNET Token Plan 实测可用（14 个，均需精确大小写）
智谱：`GLM-5.2` / `GLM-5.1` / `GLM-5`
月之暗面：`Kimi-K3` / `Kimi-K2.7-Code` / `Kimi-K2.6` / `Kimi-K2.5`
DeepSeek：`DeepSeek-V4-Flash` / `DeepSeek-V4-Pro` / `DeepSeek-V4-Flash-0731`
MiniMax：`M3` / `M2.7` / `M2.5`
Qwen：`Qwen3.8-Max`

> 文档列但实测 403 的：DeepSeek-V3.2、MiMo-V2.5-Pro、GLM-5.2-Base 系列等，勿选。

### 硅基流动常用（带组织前缀）
- `deepseek-ai/DeepSeek-V4-Flash`（1M ctx，输出 393K）
- `zai-org/GLM-5.2`（付费旗舰）
- 完整列表以 `GET /v1/models` 为准

## 6. 故障排查流程（按序执行）

1. **先粗测**：curl /models + 一次 chat/completions（见第 3 节）→ 区分「API 问题」还是「DEEPCODE 集成问题」。
2. **API 问题** → 对照第 4 节踩坑表逐条查：密钥前缀 → 模型 ID 大小写 → 参数方言（max_tokens vs max_completion_tokens）→ 余额/套餐。
3. **集成问题** → 检查链路三段：
   - `grep -n "SCNET_TP_API_KEY\|SILICONFLOW_API_KEY" .env`（密钥在不在）
   - `grep -n "scnet\|siliconflow" core/mcp_servers/router_mcp_server.py`（provider 注册了没）
   - `grep -n "scnet\|siliconflow" .deepcode/cli/cli.js`（菜单项在不在）+ settings.json provider 块
   - 全部就位仍不行 → **重启 deepcode 再试**。
4. **仍失败** → 调用 MCP `router_status` 看 active provider 与通道健康。

## 7. 参考记忆

- `scnet-token-plan-setup` — Token Plan 完整配置、抵扣表、套餐档位
- `scnet-token-plan-available-models-2026-08-23` — 套餐内/套餐外模型清单（实时更新）
- `scnet-model-id-and-max-tokens-pitfalls` — SCNet 两大坑详述
- `ds-v4flash-three-endpoints` — 官方/硅基/SCNET 三端点对比报告（本地: `F:\DS-HARNESS\llm-bench\docs\v4flash-three-endpoints.md`）