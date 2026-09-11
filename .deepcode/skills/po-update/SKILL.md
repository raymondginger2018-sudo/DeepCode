---
name: po-update
description: 更新 CHINA MACRO PURCHASE ADB 的 PO SUMMARY Excel——从散落的供应商 PI 单据抓取新采购订单数据、按供应商代号自动填供应商/产品类型、用 add_po.py 追加并自动对齐样式与合计公式、按订单号归置文件。当用户说"更新PO SUMMARY"、"加新订单/PO"、"把某订单录入PO表"、"PO数据抓取/导入"、"按订单号建目录/整理PO文件" 时使用。也用于排查 PO 金额/供应商录错、批量录入多个新 PO。基于 2026-08 实战经验，持续迭代完善。
---

# PO UPDATE — PO SUMMARY 维护方法论

> 这份 skill 沉淀了 2026-08 更新 China Macro Purchase ADB PO Summary 的全部实战经验
> （含多次录入踩坑后的修正方案）。**每次更新后把新学到的经验补进本文档**，逐步完善。

**目标文件**：`E:\PO SUMMARY ADB2025\CHINA MACRO PURCHASE ADB REV 2 2026-08-24.xlsx`
（注意：文件名带日期，实际使用**最新日期**那份，或用户指定的那份。）

**PO 数据源**：E 盘根目录 / 各订单号命名目录里的供应商 PI（PDF 扫描件 / Excel / 高清 PDF）。

---

## 铁律（先读，违反必错）

1. **供应商代号决定一切**：PO 号格式 `[E/S]<代号>-<项目段>-<序号>-<年份>`，**首段数字 = 供应商代号**。`supplier_codes.json` 里每个代号锁定了四样：SUPPLIER / PRODUCT TYPE / COMMERCIAL CONDITIONS / PRODUCT WARRANTY。**不要靠读图认供应商**，用代号查表——我犯过把 DAZHI 读成 JINHUA、GOLDENSUN 读成 HONG 的错。
2. **金额只认 TOTAL 行，且必须是最后一页 / 整单的 TOTAL**：多页 PI 的 TOTAL 通常在**最后一页**。我犯过把第 1 页局部小计当整单金额的错（E64/E75/E8A/S3A 各错一次）。**凡有多个 PDF/多页，务必读带 TOTAL 的那页**，且核对件数/单价是否自洽。
3. **提供 Excel 或高清 PDF 优先**：供应商 PI 若有 Excel 源文件（.xls/.xlsx）或高清 PDF，用 openpyxl/xlrd 或高清图读，**不要读模糊扫描件**——我多次因扫描件模糊读错（535 看成 335、69.50 看成 58.50、22897.5 看成 17842.5）。
4. **写入用 add_po.py，不要手写 openpyxl 插行**：仓库里 [add_po.py](add_po.py) 会自动：对齐样式（Arial 14 / mm-dd-yy / #,##0.00 / 0.00_);[Red]）、扩展合计公式、更新 LASTLY UPDATED、写前备份。**手写 insert_rows 会损坏 Excel 结构**（我踩过，导致文件"已修复"、E64 行丢值）。
5. **EX-WORKING DATE(L) 和 PAYMENT STATUS(M) 两列留空**——用户自己填，不要默认写 TBA 或付款条件。
6. **必须有签署版 PI（签单）才录入**：邮件里必须有 `SIGNED PI` 或供应商盖章/签字的 PI 才算有效订单。只有 PO 没有签单的不录入 SUMMARY——S2-100-49-26 就是例子（只有 G10 PI 无签单，不录入）。
7. **录入前先查 PO 是否已存在**：`add_po.py` 不会检查重复，每次运行都会追加新行。**先读 SUMMARY 检查 PO 号是否已有空行**（PO 可能提前录入留空 FOB/CBM 等签单），有则更新旧行而非追加——S4-100-71-26 就是例子（Row 68 已有空行，又追加了 Row 89）。
8. **CONTAINER QTY = TOTAL CBM / 65**：O 列（柜数）= P 列（CBM）÷ 65。`add_po.py` 已自动计算，手动更新时也要遵守。
9. **手写插入行后必须复制模板样式**：用 openpyxl 手动 `insert_rows` 后，新行默认是等线/11pt/General。**必须从模板行（Row 8）逐列 `copy()` font/alignment/number_format/border/fill**——否则日期显示为 yyyy-mm-dd、金额无千分位、与老行格式不一致。铁律 #4 的 `add_po.py` 已内置此逻辑，手写时别忘。
10. **CONFIRMATION DATE (I 列) = 签单邮件收到日期**：不是 PI 上的 Issued Date，而是 Outlook 里含 SIGNED PI 附件的那封邮件的 `ReceivedTime`。需用 `GetTable` 遍历找到带 "SIGNED" 附件的邮件取其日期。
11. **PAYMENT STATUS (M 列) 格式**：`{百分比}% USD {金额} ON {日期}`。百分比 = 付款金额 ÷ FOB 金额 × 100（**不是固定 30%**，需按实际算）。日期优先从水单 PDF 内容提取，提取不到就用文件修改时间（= 邮件收到日期）。
12. **TT 水单分发**：付款水单从临时目录分发到 `E:\OPERATION\<PO号>(CM26)\` 对应目录后，源目录可删除。匹配规则：文件名含 PO 号 → 找 E:\OPERATION 下含该 PO 号的目录。

---

## 工作流

### 步骤 0：从 Outlook 邮件下载 PO 附件

当 PO 目录还没 PI 文件时，优先从 Outlook 邮件里拉附件。

**前提**：Outlook (Classic) 已登录 `raymond@slatam.com`。

**方法 A：GetTable（推荐，秒级）**——用 DASL 过滤 + GetTable 快速枚举，避免 Restrict 在 19000+ 封邮件里超时。

```powershell
$outlook = New-Object -ComObject Outlook.Application
$ns = $outlook.GetNamespace("MAPI")
$account = $ns.Accounts | Where-Object { $_.SmtpAddress -eq "raymond@slatam.com" }
$store = $account.DeliveryStore
$inbox = $store.GetDefaultFolder(6)

# GetTable 秒级完成，不超时
$table = $inbox.GetTable('@SQL="urn:schemas:httpmail:subject" LIKE ''%<PO号>%''')
$downloaded = @{}
$dest = "E:\<PO号>"
while (-not $table.EndOfTable) {
    $row = $table.GetNextRow()
    $item = $ns.GetItemFromID($row['EntryID'], $store.StoreID)
    for ($i = 1; $i -le $item.Attachments.Count; $i++) {
        $att = $item.Attachments.Item($i)
        $key = "$($att.FileName)|$($att.Size)"
        if (-not $downloaded.ContainsKey($key)) {
            $path = Join-Path $dest $att.FileName
            $att.SaveAsFile($path)
            $downloaded[$key] = $path
        }
    }
}
```

**方法 B：winapp 搜 + COM 取值**——当 COM 搜索不可用时，用 winapp MCP 在 Outlook 搜索框输入 PO 号，然后用 `$outlook.ActiveExplorer().Selection` 取当前选中邮件并下载附件。

```powershell
# winapp 端：type_text SearchBoxTextBoxAutomationId → 输入 PO 号 → press_key RETURN
# COM 端取选中邮件：
$sel = $outlook.ActiveExplorer().Selection
$item = $sel.Item(1)
# 然后同上循环 $item.Attachments
```

**注意事项**：
- `Restrict`/`Find` 在 19000+ 封邮件里**必定超时**（>2 分钟），用 `GetTable` 替代。
- 附件去重用 `文件名+字节数` 做 key。
- 签名图片（image001~011.png/jpg、InsertPic_*.jpg）和业务文档混在一起，事后人工筛选。

### 步骤 1：查 PO 是否已在 SUMMARY 中
```python
python -c "import openpyxl; wb=openpyxl.load_workbook(r'E:\PO SUMMARY ADB2025\CHINA MACRO PURCHASE ADB REV 2 2026-08-24.xlsx', data_only=True); ws=wb['PO SUMMARY']; [print(f'Row {r}: PO={ws.cell(r,5).value} FOB={ws.cell(r,10).value} CBM={ws.cell(r,16).value}') for r in range(8, ws.max_row+1) if ws.cell(r,5).value and '<PO号>' in str(ws.cell(r,5).value)]"
```
- 如果 PO 已有空行（FOB/CBM 为空）→ **更新旧行，不要追加新行**（铁律 #7）。
- 如果 PO 不存在 → 继续步骤 2。

### 步骤 2：核对供应商代号
```python
python -c "import json;m=json.load(open(r'F:\DEEPCODE\.deepcode\skills\po-update\supplier_codes.json'));print(m)"
```
拿 PO 号首段数字查 `supplier_codes.json`，确定 SUPPLIER 和默认 PRODUCT TYPE。
（supplier_codes.json 是用户校正过的权威映射，见下。）

### 步骤 3：确认有签署版 PI（签单）
- 邮件附件中必须有 `SIGNED PI` 或供应商盖章/签字的 PI 文件。
- 只有 PO 没有签单 → **不录入**（铁律 #6）。
- 确认后继续步骤 4。

### 步骤 4：抓取金额——只认 TOTAL
- **打开 PI**：Excel/.xls 用 openpyxl/xlrd；PDF 用 PyMuPDF 渲染，**先查页面方向**（横排图易读错，先转正）。
- **定位 TOTAL**：找 "TOTAL" / "SAY TOTAL" / "Total Amount(USD)" / "NEW TOTAL" 行。**必须确认是整单的 TOTAL**（金额 = 各明细行之和才可信），不是某个 item 的行总计。
- 记录：PO号 / 供应商 / 产品 / FOB总额 / CBM / 日期。

### 步骤 5：写入（新增或更新）
```bash
python F:\DEEPCODE\.deepcode\skills\po-update\add_po.py --json orders.json
```
`orders.json` 每项：
```json
{"po":"S64-100-63-26","fob":6600.00,"country":"PANAMA","date":"2026-08-14","cbm":16.8,"pdf":"E:/S64-100-63-26/xxx.pdf"}
```
**只要给 `po`**，`supplier`/`product` 会自动按代号查表填入；没给 `fob`/`cbm` 则留空。
- **更新已有 PO**：直接用 openpyxl 填对应行的 FOB/CBM/Date，然后删除旧 TOTAL 公式、更新合计行、保存。**不要用 add_po.py 追加重复行**。

### 步骤 6：按订单号归置文件
把该 PO 的 PI/相关单据移到 `E:\<PO号>` 目录（若目录已存在）。不要乱建目录；缺目录的订单文件**留在原处**，除非用户明确要建。

### 步骤 6：验证
读回 SUMMARY 确认：新增行值正确、样式对齐、合计公式 `SUM(J8:J...)` 覆盖新行、L/M 列留空。

---

## 供应商代号权威映射（supplier_codes.json）

由用户校正，**已确认不变**。关键几条（其余见 json 文件）：

| 代号 | 供应商 | 默认产品 | 商业条款 | 质保 |
|---|---|---|---|---|
| 1 | CJF | OFFICE FURNITURE | 100% 30 DAYS AFTER LOADING | 2 YEARS |
| 2 | KC | OFFICE CHAIRS | 100% 30 DAYS AFTER LOADING | STRUCTURE: 5Y, UPHOLSTERY: 2Y |
| 64 | DAZHI | SCHOOL FURNITURE | 50% DEPOSIT, 50% ONE MONTH CREDIT | 5 YEARS |
| 73 | JE GROUP | OFFICE CHAIRS | 30% DEPOSIT, 70% BEFORE SHIPMENT. | 5 YEARS |
| 75 | HUADU | STEEL FURNITURE | 30% DEPOSIT, 70% BEFORE SHIPMENT. | 1 YEAR |
| 7A | GOLDENSUN | PLASTIC CHAIR | 30% DEPOSIT, 70% BEFORE SHIPMENT. | 2 YEARS |
| 3A | MASYOUNGER | STEEL CABINET | 30% DEPOSIT, 60% BEFORE SHIPMENT, 10% ONE MONTH AFTER LOADING. | 3 YEARS |
| 8A | BOKE | OFFICE CHAIR | 30% DEPOIST, 50% BEFORE SHIPMENT, 20% ONE MONTH AFTER LOADING. | 5 YEARS |
| 19 | KANO | OFFICE FURNITURE | 30% DEPOSIT, 70% BEFORE SHIPMENT. | 5 YEARS |
| 15 | CHENGMAI | OFFICE CHAIR | 30% DEPOIST, 50% BEFORE SHIPMENT, 20% ONE MONTH AFTER LOADING. | 3 YEARS |

> 所有 34 个供应商的完整四字段映射见 `supplier_codes.json`。`add_po.py` 会按 PO 号代号自动查表填入 SUPPLIER / PRODUCT / CONDITIONS / WARRANTY，只需提供 `po` + `fob` + `cbm` + `date` + `country`。

---

## 历史踩坑记录（每次更新补一条）

- **2026-08-31**（CM26 批量录入）：
  - 6 个 PO 批量处理：S1-100-41-26、S1-100-75-26、S2-100-49-26、S2-100-76-26、S4-100-77-26、S9A-100-72-26。
  - 5 个有签单（均含 SIGNED PI），S9A-100-72-26 无邮件无附件，跳过。
  - S2-100-49-26：之前无签单未录入，现在有了签单（SIGNED PI S2-100-49-26.pdf），补录。
  - 目标文件：`CHINA MACRO PURCHASE ADB REV 2 2026-08-31.xlsx`（新版本）。
  - 用户要求新订单放在 CM26 ORDERS 标记行的**上方**，旧行更新不追加。
  - 经验：批量处理时先查重再操作，一次性 `insert_rows` 多行再逐行写数据。
- **2026-09-07**（S15-100-74-26 + S7-100-73-26 格式教训）：
  - 手写 `insert_rows` 后新行默认等线/11pt/General，与老行 Arial/14pt 不一致。必须从模板行 copy 样式。
  - CONFIRMATION DATE (I 列) 应取签单邮件的 ReceivedTime，不是 PI 上的日期。
  - 用户指定目录在 `E:\OPERATION\<PO号>` 而非 `E:\<PO号>`，需按用户指示建目录。
  - 新增铁律 #9（手写插入行必须复制模板样式）和 #10（CONFIRMATION DATE = 签单邮件日期）。
- **2026-08-26**（四字段自动匹配）：
  - `supplier_codes.json` 扩展为四字段：`supplier` / `product` / `conditions` / `warranty`，覆盖全部 34 个供应商。
  - `add_po.py` 新增 `lookup_conditions()` 和 `lookup_warranty()`，录入时只需 `po` + `fob` + `cbm` + `date` + `country`，其余四列自动查表填入。
  - 更新铁律 #1：供应商代号决定一切（四样全锁）。
- **2026-09-09**（PAYMENT STATUS + TT 分发）：
  - PAYMENT STATUS 格式确认：`{百分比}% USD {金额} ON {日期}`，百分比按实际付款/FOB 算，不是固定 30%。
  - E73-100-59-26 教训：脚本跑时 SUMMARY 里 FOB 是旧值 $75,805，算出 28%；用户后来改成正确值 $70,805，应为 30%。**批量更新 PAYMENT STATUS 前必须验证 FOB 是否最新**。
  - TT 水单分发流程：从临时目录按文件名匹配 PO 号 → 复制到 `E:\OPERATION\<PO号>(CM26)\` → 删除源目录。
  - 新增铁律 #11（PAYMENT STATUS 格式 + 百分比计算）和 #12（TT 水单分发规则）。
- **2026-08-26**（CONTAINER QTY 公式）：
  - 新增铁律 #8：CONTAINER QTY = TOTAL CBM / 65。
  - 全表 73 行已批量更新（跳过 2 行 CBM 为表达式的）。
  - `add_po.py` 已修改：O 列自动计算 `cbm/65`，新增 `_to_float()` 辅助函数。
- **2026-08-26**（S4-100-71-26 重复行 + S2-140/S3-140）：
  - S4-100-71-26：Row 68 已有空行（提前录入留空），未检查又追加了 Row 89。手动修正：更新 Row 68 数据、删除 Row 89、修复合计公式。
  - 新增铁律 #7：录入前先查 PO 是否已存在，有则更新旧行而非追加。
  - S2-140-01-26 和 S3-140-03-26：均无签署版 PI，不录入。
  - 工作流步骤重新编号：步骤 1 查重、步骤 3 确签、步骤 5 区分新增/更新。
- **2026-08-24**（S2-100-49-26 + 签单规则）：
  - S2-100-49-26：从 5 封邮件下载 3 个附件（PO + PI PDF + 图片），但无签署版 PI，不录入 SUMMARY。
  - 新增铁律 #6：必须有签署版 PI（SIGNED PI）才录入 PO SUMMARY。
  - 新增"步骤 0：从 Outlook 邮件下载 PO 附件"——两种方法：GetTable（秒级）和 winapp 搜+COM 取值。
  - **关键发现**：`Items.Restrict`/`Items.Find` 在大邮箱（19000+）里必定超时，用 `Folder.GetTable()` 替代，秒级返回。
  - `GetTable` 返回的 Row 不含 `ReceivedTime`，需用 `GetItemFromID(EntryID)` 取完整 MailItem 再下载附件。
  - S34-100-44-26：从 20 封邮件下载 73 个附件，含 PO、PI、签署版 PI、供应商资料。
- **2026-08-24**（winapp 修复）：
  - winapp-mcp 用 `npx -y` 每次下载 152MB 包导致超时/EOF，`npm install -g winapp-mcp` 全局安装后解决。
  - 配置从 `npx -y winapp-mcp` 改为 `node C:/Users/raymo/AppData/Roaming/npm/node_modules/winapp-mcp/bin/winapp-mcp.js`。
- **2026-08-23**（首次）：
  - 只读 PI 第 1 页 → 把局部小计当整单 TOTAL，4 单错（E64/E75/E8A/S3A）。
  - 模糊扫描件数字读错：535→335（E2）、6,600→66,600（S64）、22,897.5→17,842.5（E7）。
  - 供应商读错：DAZHI→JINHUA（E64/S64）、GOLDENSUN→HONG（S7A）。
  - **教训**：金额只认 TOTAL 页；供应商用代号查表；优先 Excel/高清 PDF。

---
