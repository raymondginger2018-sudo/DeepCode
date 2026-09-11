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

1. **供应商代号决定供应商**：PO 号格式 `[E/S]<代号>-<项目段>-<序号>-<年份>`，**首段数字 = 供应商代号**，映射关系**不变**（如 `64` = DAZHI，`2` = KC，`1` = CJF）。看 `supplier_codes.json`。**不要靠读图认供应商**，用代号查表——我犯过把 DAZHI 读成 JINHUA、GOLDENSUN 读成 HONG 的错。
2. **金额只认 TOTAL 行，且必须是最后一页 / 整单的 TOTAL**：多页 PI 的 TOTAL 通常在**最后一页**。我犯过把第 1 页局部小计当整单金额的错（E64/E75/E8A/S3A 各错一次）。**凡有多个 PDF/多页，务必读带 TOTAL 的那页**，且核对件数/单价是否自洽。
3. **提供 Excel 或高清 PDF 优先**：供应商 PI 若有 Excel 源文件（.xls/.xlsx）或高清 PDF，用 openpyxl/xlrd 或高清图读，**不要读模糊扫描件**——我多次因扫描件模糊读错（535 看成 335、69.50 看成 58.50、22897.5 看成 17842.5）。
4. **写入用 add_po.py，不要手写 openpyxl 插行**：仓库里 [add_po.py](add_po.py) 会自动：对齐样式（Arial 14 / mm-dd-yy / #,##0.00 / 0.00_);[Red]）、扩展合计公式、更新 LASTLY UPDATED、写前备份。**手写 insert_rows 会损坏 Excel 结构**（我踩过，导致文件"已修复"、E64 行丢值）。
5. **EX-WORKING DATE(L) 和 PAYMENT STATUS(M) 两列留空**——用户自己填，不要默认写 TBA 或付款条件。

---

## 工作流

### 步骤 1：确认目标文件和要更新的批次
问清楚或判断：更新哪份 SUMMARY、要加哪些新 PO（从 E 盘根目录 / 订单目录找 8/20 之后的新 PI）。

### 步骤 2：核对供应商代号
```python
python -c "import json;m=json.load(open(r'F:\DEEPCODE\.deepcode\skills\po-update\supplier_codes.json'));print(m)"
```
拿 PO 号首段数字查 `supplier_codes.json`，确定 SUPPLIER 和默认 PRODUCT TYPE。
（supplier_codes.json 是用户校正过的权威映射，见下。）

### 步骤 3：抓取金额——只认 TOTAL
- **打开 PI**：Excel/.xls 用 openpyxl/xlrd；PDF 用 PyMuPDF 渲染，**先查页面方向**（横排图易读错，先转正）。
- **定位 TOTAL**：找 "TOTAL" / "SAY TOTAL" / "Total Amount(USD)" / "NEW TOTAL" 行。**必须确认是整单的 TOTAL**（金额 = 各明细行之和才可信），不是某个 item 的行总计。
- 记录：PO号 / 供应商 / 产品 / FOB总额 / CBM / 日期。

### 步骤 4：写入 add_po.py
```bash
python F:\DEEPCODE\.deepcode\skills\po-update\add_po.py --json orders.json
```
`orders.json` 每项：
```json
{"po":"S64-100-63-26","fob":6600.00,"country":"PANAMA","date":"2026-08-14","cbm":16.8,"pdf":"E:/S64-100-63-26/xxx.pdf"}
```
**只要给 `po`**，`supplier`/`product` 会自动按代号查表填入；没给 `fob`/`cbm` 则留空。

### 步骤 5：按订单号归置文件
把该 PO 的 PI/相关单据移到 `E:\<PO号>` 目录（若目录已存在）。不要乱建目录；缺目录的订单文件**留在原处**，除非用户明确要建。

### 步骤 6：验证
读回 SUMMARY 确认：新增行值正确、样式对齐、合计公式 `SUM(J8:J...)` 覆盖新行、L/M 列留空。

---

## 供应商代号权威映射（supplier_codes.json）

由用户校正，**已确认不变**。关键几条（其余见 json 文件）：

| 代号 | 供应商 | 默认产品 |
|---|---|---|
| 1 | CJF | OFFICE FURNITURE |
| 2 | KC | OFFICE CHAIRS |
| 64 | DAZHI | SCHOOL FURNITURE |
| 73 | JE GROUP | OFFICE CHAIRS |
| 75 | HUADU | STEEL FURNITURE |
| 7A | GOLDENSUN | PLASTIC CHAIR |
| 3A | MASYOUNGER | STEEL CABINET |
| 8A | BOKE | OFFICE CHAIR |
| 19 | KANO | OFFICE FURNITURE |
| 15 | CHENGMAI | OFFICE CHAIR |

> 同一供应商可做不同产品（如 HUADU 做过 STEEL FURNITURE 也做过 FILE CABINET），**产品类型以该单 PI 为准**；供应商代号只锁"供应商"。

---

## 历史踩坑记录（每次更新补一条）

- **2026-08-23**（首次）：
  - 只读 PI 第 1 页 → 把局部小计当整单 TOTAL，4 单错（E64/E75/E8A/S3A）。
  - 模糊扫描件数字读错：535→335（E2）、6,600→66,600（S64）、22,897.5→17,842.5（E7）。
  - 供应商读错：DAZHI→JINHUA（E64/S64）、GOLDENSUN→HONG（S7A）。
  - **教训**：金额只认 TOTAL 页；供应商用代号查表；优先 Excel/高清 PDF。

---
