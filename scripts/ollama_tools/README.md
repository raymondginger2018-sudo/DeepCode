# 🤖 Ollama 本地工具集 (scripts/ollama_tools)

本地 Ollama (qwen2.5:3b / deepseek-r1:1.5b / nomic-embed-text) 驱动的五大工具，全部零成本、离线可用。

## 工具一览

| # | 工具 | 功能 | 入口 |
|:-:|:-----|:-----|:-----|
| 0 | `ollama_client.py` | 公共客户端 (generate/embed) | 库文件，被各工具复用 |
| 1 | `git_commit_msg.py` | Git 提交信息生成 | CLI + git hook |
| 2 | `daily_review.py` | 每日复盘自动生成 | CLI → knowledge-vault |
| 3 | `comment_normalize.py` | 代码注释规范化 | CLI (dry-run/apply) |
| 4 | `log_classifier.py` | 日志异常分类 | CLI + 管道 |
| 5 | `rag_ask.py` | RAG 知识问答 | CLI (索引+问答) |
| 6 | `prepare_data.py` | 复盘数据准备器 (检查+拉取缺失源) | CLI (复盘前置) |

---

## ① Git 提交信息生成

```bash
# 手动生成 (未暂存改动)
python scripts/ollama_tools/git_commit_msg.py

# 暂存区改动
python scripts/ollama_tools/git_commit_msg.py --staged

# 从 diff 文件
python scripts/ollama_tools/git_commit_msg.py --diff-file patch.txt

# 安装 hook → 每次 git commit 自动生成 (merge/amend 跳过)
python scripts/ollama_tools/git_commit_msg.py --install-hook

# 卸载 hook
python scripts/ollama_tools/git_commit_msg.py --uninstall-hook
```

已安装 ✅：`F:\DEEPCODE\.git\hooks\prepare-commit-msg`
（不想用生成的提交信息时，正常编辑提交信息文件保存即可覆盖）

## ② 每日复盘自动生成

```bash
# 最新交易日 (默认: 自动先准备数据, 再生成复盘)
python scripts/ollama_tools/daily_review.py

# 指定日期 / 只预览 / 跳过数据准备 / 自定义输出 / 指定模型
python scripts/ollama_tools/daily_review.py --date 20260731 --dry-run
python scripts/ollama_tools/daily_review.py --no-prepare
python scripts/ollama_tools/daily_review.py --out my_review.md
python scripts/ollama_tools/daily_review.py --model yaogu-analyst   # 默认就是 yaogu-analyst
```

输出：`knowledge-vault/notes/市场复盘_YYYYMMDD.md`（Obsidian 格式，含 frontmatter）
目标日自动取**最近有效交易日**（涨停数>0），避免空数据日（如周末误记的 20260801）。

**数据准备（--prepare 默认开启）**：生成前先调 `prepare_data.py` 检查并拉取缺失数据源，
保证复盘数据来源不缺失。完整流程见 [⑥ prepare_data.py](#⑥-prepare_data.py)。

**复盘 6 节结构**（数据源 4 类，无数据的章节自动省略）：
| 章节 | 数据源 |
|:--|:--|
| 一、市场概况 | daily_review_meta + market_state_history |
| 二、板块热度 | daily_sector（涨幅/资金流/动量综合热度分 Top8）|
| 三、妖股引擎（重点）| 知识库「妖股情绪周期日历」+「{date}-妖股核心候选」笔记 |
| 四、板块轮动（含主力净流入）| daily_sector + sector_fund_flow |
| 五、资金与信号 | sector_fund_flow（净流入/净流出排行 + 轮动信号）|
| 六、风险提示与明日关注 | 综合 |

## ③ 代码注释规范化

```bash
# 预览建议 (默认，不修改)
python scripts/ollama_tools/comment_normalize.py --path ./src

# 确认后写入
python scripts/ollama_tools/comment_normalize.py --path ./src --apply

# 单文件 / 指定扩展名
python scripts/ollama_tools/comment_normalize.py --file app.py --apply
python scripts/ollama_tools/comment_normalize.py --path . --glob "*.py" --apply
```

安全设计：只改注释内容，保留缩进/注释符号；自动跳过 TODO/FIXME/NOTE、编码声明、纯标点噪声。

## ④ 日志异常分类

```bash
# 文件 / 目录
python scripts/ollama_tools/log_classifier.py --file app.log
python scripts/ollama_tools/log_classifier.py --dir ./logs

# 管道 (tail 场景)
tail -100 app.log | python scripts/ollama_tools/log_classifier.py

# JSON 输出 (供程序消费)
python scripts/ollama_tools/log_classifier.py --file app.log --json
```

输出：级别 → 类别 → 严重度(🔴🟠🟡) → 处置建议，相同错误自动合并计数。
Ollama 不可用时自动降级为规则级统计。

**可选接入 onError hook**（不默认安装，避免侵入）：
```bash
python3 F:/DEEPCODE/scripts/ollama_tools/log_classifier.py --file <错误日志> --json
```

## ⑤ RAG 知识问答

```bash
# 首次使用先建索引 (向量化 vault + 记忆库)
python scripts/ollama_tools/rag_ask.py --rebuild

# 提问
python scripts/ollama_tools/rag_ask.py "20260731 市场温度如何？"

# 只看检索结果
python scripts/ollama_tools/rag_ask.py --retrieve-only "缠论"

# 指定知识库目录
python scripts/ollama_tools/rag_ask.py --vault ./knowledge-vault/notes "如何配置 MCP"
```

索引源：`knowledge-vault/notes/*.md` + `data/memory/memory.db`
索引文件：`scripts/ollama_tools/rag_index.db`（知识库更新后重新 `--rebuild`）

---

## ⑦ 批量补生成历史复盘 (batch_review.py)

对缺失交易日逐个执行「数据准备 → 复盘生成」：

```bash
python scripts/ollama_tools/batch_review.py                    # 补全部缺失
python scripts/ollama_tools/batch_review.py --date 20260713 20260714   # 指定日期
python scripts/ollama_tools/batch_review.py --dry-run          # 只列待补清单
python scripts/ollama_tools/batch_review.py --skip-prepare     # 数据已齐, 跳过准备
```

待补清单 = `daily_review_meta` 有效交易日 − 已生成复盘文件。每条约 1~2 分钟（含数据准备）。
数据源无法回补的历史缺口（如 sector_fund_flow 7/8~7/15、妖股核心候选）章节自动省略，不编造。

---

## ⑥ 复盘数据准备器 (prepare_data.py)

生成复盘前确保所有数据源齐备 — 检查 → 缺失则拉取 → 复查。

```bash
# 准备最近交易日全部数据源
python scripts/ollama_tools/prepare_data.py

# 指定交易日 / 只检查不拉取 / 只准备指定源
python scripts/ollama_tools/prepare_data.py --date 20260731
python scripts/ollama_tools/prepare_data.py --check-only
python scripts/ollama_tools/prepare_data.py --source yaogu   # review/flow/yaogu
```

**数据源 ↔ 采集入口映射**（符合回退链规范: SQLite → **Tushare** → AkShare → 东财直连）：

| 数据源 | 复盘章节 | 采集入口 | 数据源 |
|:--|:--|:--|:--|
| daily_review_meta/ladder/sector | 市场概况/板块热度/板块轮动 | `daily_review_db.py update` | **Tushare limit_list_d** → 回退 akshare/东财 |
| sector_fund_flow | 板块轮动(主力资金)/资金与信号 | `prepare_data.prepare_sector_flow` | **Tushare moneyflow_ind_ths** → 回退东财 |
| 妖股笔记(情绪周期/核心候选) | 妖股引擎 | `run_monster_update.py --skip-backfill` | 东财 push2ex 直连 |

Tushare 接口（`quant_trading/data/tushare_engine.py` 新增）：
- `fetch_limit_list(trade_date)` → `pro.limit_list_d`（涨停/连板数/首封时间/行业）
- `build_ladder_from_tushare(trade_date)` → 涨停梯队（与 build_ladder_table 同格式，供 daily_review_db 优先使用）
- `fetch_sector_moneyflow(trade_date)` → `pro.moneyflow_ind_ths`（板块净流入，亿元）

目标交易日 = **最近有效交易日**（`daily_review_meta` 中涨停数>0 的最新日期）。

> ℹ️ 板块热度取自 `daily_sector`（涨幅/资金流/动量综合热度分），**每日全覆盖**，不依赖实时温度表。

---

## 依赖

- 本机 Ollama 运行中 (`ollama serve`)，模型：`qwen2.5:3b`、`nomic-embed-text`（RAG 需要）、`deepseek-r1:1.5b`（设置分析）
- 纯 Python 标准库，无第三方依赖
- 数据准备 (prepare_data) 依赖量化模块：`quant_trading`（Tushare/AkShare/东方财富）

## 增强能力（Ollama 开源定制）

### 领域定制模型 `yaogu-analyst`
基于 qwen2.5:3b 固化的 A 股妖股/短线分析专家（Modelfile: `Modelfile.yaogu-analyst`）：
```bash
ollama create yaogu-analyst -f scripts/ollama_tools/Modelfile.yaogu-analyst
```
特性：情绪周期/连板梯队/资金信号/风险控制分析框架 + 不编造数据 + 仓位建议。

### JSON 结构化输出
`ollama_client.generate(format=...)` 支持 Ollama JSON 模式，输出保证合法 JSON：
```python
from ollama_client import generate
out = generate("...", format={"type": "object", "properties": {"code": {"type": "string"}}})
```

### 服务并行调优（已设置，重启 Ollama 生效）
```bash
OLLAMA_NUM_PARALLEL=4       # 并行请求
OLLAMA_MAX_LOADED_MODELS=3  # 多模型常驻 (42GB 内存)
OLLAMA_KEEP_ALIVE=-1        # 模型永不卸载
OLLAMA_CTX_SIZE=8192        # 上下文
```
