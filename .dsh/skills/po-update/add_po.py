#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
add_po.py — 向 CHINA MACRO PURCHASE ADB 追加新 PO 订单，样式自动对齐老订单。

用法（命令行）：
    python add_po.py "PO号" "供应商" "产品类型" FOB 国家 日期 [CBM] [付款条件] [商务条件]

或（批量，JSON 文件）：
    python add_po.py --json orders.json

JSON 格式：
    [ {"po":"S1-100-77-26","supplier":"CJF","product":"OFFICE CHAIR","fob":12345.67,
       "country":"PANAMA","date":"2026-08-25","cbm":10.5,"pay":"100% T/T BEFORE",
       "warranty":"2 YEARS","conditions":"..."}, ... ]

供应商自动填充： supplier/product 可省略。若省略，工具按 PO 号首段数字段（供应商代号）
从 supplier_codes.json 自动查供应商名和默认产品类型（如 S64-100-63-26 -> 代号64 -> DAZHI/SCHOOL FURNITURE）。

样式保证：
  - 字体 Arial 14，水平/垂直居中（文本列 center，数字列 center）
  - 日期列 mm-dd-yy，FOB #,##0.00，柜数 0.0_);[Red](0.0)，CBM 0.00_);[Red](0.00)
  - 自动扩展合计公式 （J/O 列 SUM 范围）
  - 更新 'LASTLY UPDATED' 为当天
"""
import sys, io, json, os, shutil, re
from copy import copy
from datetime import datetime, date
from openpyxl.utils import get_column_letter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import openpyxl

SRC = r'E:\PO SUMMARY ADB2025\CHINA MACRO PURCHASE ADB REV 2 2026-08-24.xlsx'
SHEET = 'PO SUMMARY'
HEADER_ROW = 7          # 表头行
DATA_START = 8          # 数据区起始
STYLE_ROW = 84          # 样式模板行（取一个标准数据行）

# 供应商代号对照表：PO号首段数字 = 供应商代号（如 64=DAZHI，2=KC，1=CJF）
CODE_MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'supplier_codes.json')
_CODE_MAP = {}
if os.path.exists(CODE_MAP_FILE):
    try:
        _CODE_MAP = json.load(open(CODE_MAP_FILE, encoding='utf-8'))
    except Exception:
        _CODE_MAP = {}

def supplier_code(po):
    """从 PO 号提取供应商代号（PO 号格式 [E/S]<代號>-...，如 S64-100-63-26 -> 64）。"""
    if not po:
        return None
    m = re.match(r'^[ES](\d+[A-Z]?)-', str(po).strip().upper())
    return m.group(1) if m else None

def lookup_supplier(po):
    """按 PO 号代号查供应商名（来自对照表），找不到返回 None。"""
    code = supplier_code(po)
    if code and code in _CODE_MAP:
        return _CODE_MAP[code].get('supplier')
    return None

def lookup_product(po):
    """按 PO 号代号查默认产品类型，找不到返回 None。"""
    code = supplier_code(po)
    if code and code in _CODE_MAP:
        return _CODE_MAP[code].get('product')
    return None

def load():
    return openpyxl.load_workbook(SRC, data_only=False)

def find_last_data_row(ws):
    """数据区最后一个有 PO 号的行。"""
    last = DATA_START - 1
    for r in range(DATA_START, ws.max_row + 1):
        if ws.cell(r, 5).value:
            last = r
    return last

def find_total_row(ws):
    """合计行：I 列有 'TOTAL AMOUNT USD:' 的那一行。"""
    for r in range(DATA_START, ws.max_row + 1):
        v = ws.cell(r, 9).value
        if isinstance(v, str) and 'TOTAL' in v.upper():
            return r
    return ws.max_row

def build_row(info, template_row):
    """构造一行数据(dict col->value)，供写入。模板只取样式。
    supplier/product 若未显式提供，则按 PO 号的供应商代号从对照表自动查找。"""
    po = str(info.get('po', ''))
    sup = info.get('supplier') or lookup_supplier(po) or ''
    prod = info.get('product') or lookup_product(po) or 'OFFICE FURNITURE'
    return {
        1: sup.upper(),                            # A SUPPLIER
        2: prod,                                   # B PRODUCT TYPE
        3: info.get('conditions', ''),            # C COMMERCIAL CONDITIONS (use conditions or pay)
        4: info.get('warranty', '2 YEARS'),       # D PRODUCT WARRANTY
        5: info.get('po', ''),                    # E PO NUMBER
        6: info.get('country', 'PANAMA'),         # F COUNTRY
        7: _to_date(info.get('date')),            # G PO RECEIPT DATE
        8: _to_date(info.get('pi_date') or info.get('date')),  # H SUPPLIER'S PI ISSUE DATE
        9: _to_date(info.get('confirm_date')),    # I CONFIRMATION DATE
        10: info.get('fob'),                      # J FOB VALUE
        11: None,                                 # K PRODUCTION LEADTIME (formula usually; leave for now)
        12: None,                                 # L EX-WORKING DATE — 留空，由用户自己填
        13: None,                                 # M PAYMENT STATUS — 留空，由用户自己填
        14: _to_date(info.get('loading')),        # N LOADING DATE
        15: info.get('container'),                # O CONTAINER QTY
        16: info.get('cbm'),                      # P TOTAL CBM
        17: info.get('claims'),                   # Q PRODUCT CLAIMS
    }

def _to_date(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%Y/%m/%d', '%d-%m-%Y'):
        try:
            d = datetime.strptime(s, fmt)
            return d
        except ValueError:
            continue
    return None

def apply_style(ws, row, template_row):
    """把 template_row 的样式逐列复制到 row（保留 row 自己的值）。"""
    for c in range(1, ws.max_column + 1):
        src = ws.cell(template_row, c)
        dst = ws.cell(row, c)
        dst.font = copy(src.font)
        dst.alignment = copy(src.alignment)
        dst.number_format = src.number_format
        dst.border = copy(src.border)
        dst.protection = copy(src.protection)
        dst.fill = copy(src.fill)

def add_orders(orders):
    wb = load()
    ws = wb[SHEET]
    last = find_last_data_row(ws)
    total_row = find_total_row(ws)
    print('当前数据区末行 =', last, ' 合计行 =', total_row)

    # 需要在合计行之前插入 len(orders) 行
    n = len(orders)
    ws.insert_rows(total_row, amount=n)

    # 新行从 (last+1) 开始写 —— 注意 insert 之后 last 位置不变，新行在 last+1..last+n
    start = last + 1
    for i, info in enumerate(orders):
        row = start + i
        data = build_row(info, STYLE_ROW)
        for c, v in data.items():
            ws.cell(row, c).value = v
        apply_style(ws, row, STYLE_ROW)

    # 更新合计行：之前 total_row，插入 n 行后移到 total_row + n
    new_total = total_row + n
    ws.cell(new_total, 9).value = 'TOTAL AMOUNT USD:'
    ws.cell(new_total, 10).value = f'=SUM(J{DATA_START}:J{new_total-1})'
    ws.cell(new_total, 14).value = 'TOTAL CONTAINERS:'
    ws.cell(new_total, 15).value = f'=SUM(O{DATA_START}:O{new_total-1})'

    # LASTLY UPDATED
    ws['A5'].value = 'LASTLY UPDATED ' + date.today().isoformat()

    # 备份
    bak = SRC.replace('.xlsx', f'.BAK-{date.today().strftime("%Y%m%d-%H%M")}.xlsx')
    if not os.path.exists(bak):
        shutil.copy2(SRC, bak)

    wb.save(SRC)
    print(f'已追加 {n} 个订单到行 {start}..{start+n-1}')
    print(f'合计公式: J{new_total} = SUM(J{DATA_START}:J{new_total-1})')
    print(f'LASTLY UPDATED = {ws["A5"].value}')
    print(f'备份: {os.path.basename(bak)}')
    return start, start + n - 1

def main():
    args = sys.argv[1:]
    if args and args[0] == '--json':
        orders = json.load(open(args[1], encoding='utf-8'))
    elif len(args) >= 5:
        # po supplier product fob country date [cbm] [pay] [warranty]
        po, supplier, product = args[0], args[1], args[2]
        fob = float(args[3]) if args[3] not in ('', 'None') else None
        country = args[4]
        date_v = args[5] if len(args) > 5 else None
        cbm = float(args[6]) if len(args) > 6 and args[6] not in ('', 'None') else None
        pay = args[7] if len(args) > 7 else ''
        warranty = args[8] if len(args) > 8 else '2 YEARS'
        orders = [{'po': po, 'supplier': supplier, 'product': product, 'fob': fob,
                   'country': country, 'date': date_v, 'cbm': cbm, 'pay': pay, 'warranty': warranty}]
    else:
        print(__doc__)
        sys.exit(1)
    add_orders(orders)

if __name__ == '__main__':
    main()
