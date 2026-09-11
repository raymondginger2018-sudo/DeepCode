# Quant Trader — 生产部署指南

从零搭建到生产环境的完整指南。适用于 Windows Server / Linux (Ubuntu 22.04+)。

## 硬件要求

| 环境 | CPU | RAM | 磁盘 | 网络 |
|------|-----|-----|------|------|
| **最小** | 2 核 | 4 GB | 20 GB SSD | 稳定公网 |
| **推荐** | 8 核 | 16 GB | 100 GB SSD | 低延迟 |

## 目录

1. [环境准备](#1-环境准备)
2. [安装依赖](#2-安装依赖)
3. [数据管线配置](#3-数据管线配置)
4. [首次运行](#4-首次运行)
5. [定时任务](#5-定时任务)
6. [监控告警](#6-监控告警)
7. [生产检查清单](#7-生产检查清单)

---

## 1. 环境准备

### 1.1 系统依赖

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y \
    python3.12 python3.12-venv python3-pip \
    sqlite3 redis-server \
    git curl cron

# Windows
# 安装 Python 3.12 → https://python.org
# 安装 Git → https://git-scm.com
```

### 1.2 Python 虚拟环境

```bash
cd /opt
git clone <repo-url> quant-trader
cd quant-trader

python3.12 -m venv .venv
source .venv/bin/activate   # Linux
# .venv\Scripts\activate    # Windows

pip install -r requirements.txt
```

### 1.3 目录结构

```
/opt/quant-trader/
├── quant_trading/           # 核心代码
│   ├── data/                # 策略注册表 + GRPO群体
│   ├── logs/                # 结构化日志 (JSON Lines)
│   ├── kline_cache.db      # SQLite K线缓存
│   └── .env                 # API Token
├── reports/                 # Word 分析报告
├── .github/workflows/       # CI/CD
└── docs/                    # API 文档
```

---

## 2. 安装依赖

```bash
pip install -r requirements.txt

# 可选: Prometheus 监控
pip install prometheus-client

# 可选: ELK 日志管线
# 服务端安装 Filebeat + Logstash + Elasticsearch (参见第6节)

# 验证安装
python quant_trader.py cache
```

---

## 3. 数据管线配置

### 3.1 Tushare Token

```bash
# 编辑 .env 文件
nano quant_trading/.env
```

```ini
TUSHARE_TOKEN=你的token
TUSHARE_MAX_RPS=7

# Server酱微信推送 (可选)
SERVERCHAN_SENDKEY=你的key
ALERT_PHONE=13800000000
```

注册获取 Token: https://tushare.pro → 微信扫码 → 个人中心 → 接口TOKEN

### 3.2 首次数据预热 (~30分钟)

```bash
# 全市场K线缓存 (约5000只A股, 250条日线/只)
python quant_trader.py prewarm

# 校验数据新鲜度
python -c "
from quant_trading.data_cache import get_cached_kline
df = get_cached_kline('000001', 5)
print(f'最新日期: {df[\"date\"].max()}')
"

# 预热引擎评分 (后续 calibrate/grpo 依赖)
python quant_trading/batch_scorer.py --codes 100 --step 5
```

---

## 4. 首次运行

### 4.1 基础功能验证

```bash
# 市场状态
python quant_trading/market_state.py

# 单股分析
python quant_trader.py analyze 000001

# 信号统计报告
python quant_trading/backtest_engine.py --report --max-stocks 30
```

### 4.2 策略回测

```bash
# 全市场信号扫描
python quant_trading/backtest_engine.py --scan-all --signal B1 --hold 10

# 权重校准
python quant_trading/calibrate_weights.py --stocks 50 --hold 20

# GRPO 优化 (10代)
python quant_trading/grpo_optimizer.py --train --population 16 --generations 10
```

---

## 5. 定时任务

### 5.1 Linux Crontab

```bash
crontab -e
```

```cron
# 盘前 (07:30 CST = 23:30 UTC)
30 23 * * 1-5 cd /opt/quant-trader && .venv/bin/python quant_trader.py prewarm >> logs/prewarm.log 2>&1

# 盘中监控 (09:30-15:00, 每30分钟)
*/30 1-6 * * 1-5 cd /opt/quant-trader && .venv/bin/python quant_trader.py monitor --collect >> logs/monitor.log 2>&1

# 盘后 (15:30 CST = 07:30 UTC)
30 7 * * 1-5 cd /opt/quant-trader && .venv/bin/python quant_trading/batch_scorer.py --incremental >> logs/scorer.log 2>&1

# 每日收评 (16:00 CST = 08:00 UTC)
0 8 * * 1-5 cd /opt/quant-trader && .venv/bin/python quant_trader.py ladder >> logs/ladder.log 2>&1
0 8 * * 1-5 cd /opt/quant-trader && .venv/bin/python quant_trader.py hot_sector >> logs/sector.log 2>&1

# 周末: 策略优化
0 10 * * 6 cd /opt/quant-trader && .venv/bin/python quant_trading/grpo_optimizer.py --evolve >> logs/grpo.log 2>&1
```

### 5.2 Windows Task Scheduler

```powershell
# PowerShell (管理员)
$action = New-ScheduledTaskAction -Execute ".venv\Scripts\python.exe" -Argument "quant_trader.py prewarm"
$trigger = New-ScheduledTaskTrigger -Daily -At "07:30AM"
Register-ScheduledTask -TaskName "QuantPrewarm" -Action $action -Trigger $trigger
```

---

## 6. 监控告警

### 6.1 Prometheus + Grafana

```bash
# 启动指标端点
python quant_trader.py monitor

# 生成 Grafana 仪表盘
python quant_trader.py monitor --dashboard

# Docker Compose (可选: 自建 Prometheus + Grafana)
```

**docker-compose.yml:**
```yaml
version: '3'
services:
  prometheus:
    image: prom/prometheus
    ports: ["9090:9090"]
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
  grafana:
    image: grafana/grafana
    ports: ["3000:3000"]
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
```

**prometheus.yml:**
```yaml
scrape_configs:
  - job_name: 'quant_trader'
    static_configs:
      - targets: ['host.docker.internal:9090']
    scrape_interval: 30s
```

### 6.2 ELK 日志管线

```bash
# 生成配置
python quant_trading/structured_logger.py --filebeat --logstash

# 安装 Filebeat
curl -O https://artifacts.elastic.co/downloads/beats/filebeat/filebeat-8.x-amd64.deb
sudo dpkg -i filebeat-8.x-amd64.deb
sudo cp quant_trading/logs/filebeat.yml /etc/filebeat/filebeat.yml
sudo systemctl enable filebeat && sudo systemctl start filebeat
```

### 6.3 Server酱微信告警

已在 `quant_trading/.env` 中配置 `SERVERCHAN_SENDKEY`。以下事件自动推送:
- 数据刷新失败 (`prewarm_with_alert.py`)
- 缓存日期过期
- 策略信号触发 (`notifier.py`)

---

## 7. 生产检查清单

### 7.1 部署前

- [ ] Python 3.12 已安装
- [ ] `requirements.txt` 依赖完整
- [ ] Tushare Token 已配置且积分≥200
- [ ] `kline_cache.db` 已预热 (≥4000只股票)
- [ ] `engine_scores` 表已填充 (≥50只)
- [ ] 59 个单元测试全部通过: `pytest quant_trading/tests/ -v`
- [ ] 集成测试通过: `pytest quant_trading/tests/test_integration.py -v`

### 7.2 运行时

- [ ] 定时任务已配置 (crontab / Task Scheduler)
- [ ] Prometheus 指标端点可访问: `curl localhost:9090/metrics`
- [ ] Grafana 仪表盘已导入
- [ ] 日志文件正常滚动 (检查 `logs/` 目录)
- [ ] Server酱推送测试通过
- [ ] 磁盘空间 > 5 GB 余量

### 7.3 健康检查命令

```bash
# 一键健康检查
python -c "
from quant_trading.data_cache import get_cache_stats
from quant_trading.batch_scorer import get_scored_codes

stats = get_cache_stats()
print(f'K线缓存: {stats[\"codes\"]}只, {stats[\"size_mb\"]}MB, 最后更新: {stats[\"latest_update\"]}')

scored = get_scored_codes(min_scored_dates=5)
print(f'引擎评分: {len(scored)}只已评分')

# 检查测试
import subprocess
r = subprocess.run(['python', '-m', 'pytest', 'quant_trading/tests/test_core.py', '-q'], capture_output=True)
print(f'单元测试: {r.returncode == 0 and \"PASS\" or \"FAIL\"}')
"
```

### 7.4 性能基准

| 操作 | 预期耗时 |
|------|----------|
| 全市场 K线预热 (5000只) | 25-35 分钟 |
| 批量引擎评分 (100只×50日期) | 3-5 分钟 |
| 单股完整分析 (analyze_stock) | 0.3-0.6 秒 |
| 全市场信号扫描 (200只) | 1-2 分钟 |
| GRPO 10代训练 (16个体) | 2-4 分钟 |

---

## 8. 故障排除

| 问题 | 原因 | 解决 |
|------|------|------|
| `Tushare 不可用` | Token 过期或超限 | 检查 `.env` 中的 token，确认积分余额 |
| `缓存日期不是今天` | 盘前/盘中数据未更新 | Tushare 通常 15:30 后更新当日数据 |
| `engine_scores 为空` | 未运行 batch_scorer | `python quant_trading/batch_scorer.py --codes 50` |
| `SQLite database locked` | 并发写入冲突 | SQLite WAL 模式已启用，最多 4 workers |
| `pytest 未找到` | 未安装 | `pip install pytest pytest-cov` |
| `Grafana 仪表盘无数据` | Prometheus 未启动 | `python quant_trader.py monitor` 保持运行 |

---

## 9. 升级指南

```bash
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt

# 数据库迁移 (如有新增表)
python -c "from quant_trading.data_cache import init_db; init_db()"

# 重新预热评分
python quant_trading/batch_scorer.py --incremental

# 验证
python -m pytest quant_trading/tests/ -q
```
