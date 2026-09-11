# 数据架构规范 — 数据库驱动 (DB-First)

所有采/分析链路必须遵循 **SQLite 优先** 原则，禁止模块直接调外部 API。

---

## 架构层级

```
外部数据源 (Tushare/AkShare/东方财富/新浪/龙虎榜...)
      │
      ▼  （写入）
┌──────────────────────┐
│    SQLite 数据库层     │  ← 统一数据入口，负责数据新鲜度
│  kline_cache.db       │
│  data.db              │
│  market_temperature.db│
│  seat_tracker.db      │
│  ...(其他业务库)      │
└──────┬───────────────┘
      │  （读取）
      ▼
┌──────────────────────┐
│  分析模块             │  ← 只读 SQLite，不直接调外部 API
│  main_force_analyzer  │
│  chip_distribution    │
│  dragon_tiger         │
│  ...                  │
└──────────────────────┘
```

## 核心原则

### 1. 分析模块只读 SQLite

- 所有分析函数**必须先查 SQLite**，数据存在则直接返回
- `analyze_main_force()` 等分析入口的 `conn` 参数必须默认走 SQLite
- 禁止在分析逻辑中直接 `urllib.urlopen()`、`ak.stock_xxx()`、`pro.xxx()` 等外部调用

### 2. 数据采集层统一管理

- 所有外部 API 调用收敛到专门的**采集模块**（如 `batch_scorer.py`、`daily_auto_update.py` 的数据同步步骤）
- 采集模块负责：调 API → 清洗 → 写入 SQLite → 标记时间戳
- 采集模块运行策略：
  - 日线数据：收盘后**19:00 之后**自动同步（`daily_auto_update.py`）——DeepSeek 19:00 后进入半价优惠窗口，推迟到 19:00 后拉取可降低一半 API 成本
  - 盘中数据：按需刷新，写入后设置 `updated_at`（不受 19:00 窗口限制）
  - 股东/财报数据：季报/年报更新日同步
- 所有**收盘后**（15:00 之后）才可获取的日线类数据，必须排到 **19:00 之后**再拉取；任何调度任务（schtasks/cron/定时器）不得早于 19:00 触发收盘后数据同步

### 3. 数据新鲜度校验

- 每个 SQLite 表必须有 `updated_at`（符合 `sql-style.md` 规范）
- 读数据前检查时间戳：
  - 日线数据超过 1 个交易日未更新 → 先触发采集再返回
  - 盘中数据超过 30 分钟未更新 → 先触发盘中刷新
  - 季报数据超过 45 天未更新 → 触发季报同步
- 由**采集层**（非分析层）负责过期数据的重新拉取
- **成本窗口优先**：当日 19:00 之前发现日线数据过期，不得触发提前拉取——返回已有数据并标记「待 19:00 窗口同步」，等待当日 19:00 后由定时任务统一补齐（日线类数据严禁在 19:00 前以"新鲜度过期"为由绕过成本窗口）

### 4. 异常回退：SQLite 无数据时的兜底

仅当 SQLite 中**完全不存在**该股票/该日期的数据时，才允许按以下优先级回退：

```
SQLite → 本地缓存（parquet/CSV） → Tushare Pro → AkShare → 东方财富直连
```

回退获取的数据必须**立即写入 SQLite**，并标记 `data_source=回退源名称`，确保下次走 SQLite。

### 5. 熔断与限流

- 外部 API 调用必须经过 `circuit_breaker.py` 熔断器
- 连续失败 5 次以上 → 熔断该数据源 120 秒
- 熔断期间不再对该源发起请求，仅用 SQLite 已有数据降级

### 6. 禁止模式

```python
# ❌ 禁止：分析模块直接调外部 API
def analyze_something(code):
    df = ak.stock_zh_a_hist(code)         # 绕过 SQLite
    df = pro.daily(ts_code=code)           # 同上

# ✅ 正确：通过 SQLite 读取，由采集层负责数据新鲜度
def analyze_something(code, conn):
    rows = conn.execute("SELECT ... FROM kline WHERE code=?", (code,))
    # ... 只做分析，不采集
```

## 违反检测

- Code Review：发现 `import akshare`、`import tushare`、`urllib.request`、`requests.get` 出现在非采集模块中 → **必须驳回**
- 发现分析函数里有 `ak.`、`pro.`、`eastmoney` 直连调用 → **必须重构**
- 发现收盘后数据同步的调度任务（schtasks/cron/定时器）触发时间早于 19:00 → **必须驳回**，改为 19:00 后（DeepSeek 半价窗口）
