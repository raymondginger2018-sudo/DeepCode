#!/usr/bin/env python3
"""DEEPCODE PR 追踪文档自动更新脚本

查询所有 Fork 的 PR 状态，自动生成/更新 Word 文档。
可通过 DEEPCODE Hook 或 Git Hook 触发。
"""

import json
import urllib.request
import datetime
import time
import sys
from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

# 解决 Windows GBK 输出编码问题
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 配置 ──────────────────────────────────────────────
OUTPUT_PATH = Path(__file__).parent.parent / "DEEPCODE_PR_TRACKER.docx"
GITHUB_API = "https://api.github.com"

# 追踪的目标: (作者, 目标仓库)
TRACKS = [
    ("hqwlkj", "lessweb/deepcode-cli"),
    ("raymondginger2018-sudo", "lessweb/deepcode-cli"),
    ("raymondginger2018-sudo", "HKUDS/DeepCode"),
    # 自合并仓库
    ("raymondginger2018-sudo", "raymondginger2018-sudo/deepcode-cli"),
    ("raymondginger2018-sudo", "raymondginger2018-sudo/DeepCode"),
    ("hqwlkj", "hqwlkj/deepcode-cli"),
]


def fetch_prs(author: str, repo: str, max_pages: int = 5) -> list[dict]:
    """从 GitHub Search API 获取 PR 列表"""
    all_items = []
    for page in range(1, max_pages + 1):
        url = (
            f"{GITHUB_API}/search/issues"
            f"?q=type:pr+author:{author}+repo:{repo}"
            f"&per_page=100&sort=created&order=asc&page={page}"
        )
        req = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json", "User-Agent": "deepcode-cli"}
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        items = data.get("items", [])
        if not items:
            break
        all_items.extend(items)
        if len(items) < 100:
            break
    return all_items


def classify(pr: dict) -> str:
    """分类 PR 状态"""
    if pr.get("pull_request", {}).get("merged_at"):
        return "✅ MERGED"
    if pr["state"] == "closed":
        return "🔴 CLOSED"
    return "🟡 OPEN"


# ── 主流程 ─────────────────────────────────────────────
def generate_report():
    # 节流: 1小时内不重复更新
    throttle_file = OUTPUT_PATH.with_suffix(".throttle")
    if throttle_file.exists():
        try:
            last_run = float(throttle_file.read_text().strip())
            if time.time() - last_run < 3600:
                print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] Skipped (last update < 1 hour ago)")
                return
        except (ValueError, FileNotFoundError):
            pass

    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] Updating PR tracker document...")

    # 获取所有 PR 数据
    all_data = {}
    for i, (author, repo) in enumerate(TRACKS):
        if i > 0:
            time.sleep(1.5)  # 避免 GitHub API 限流
        try:
            prs = fetch_prs(author, repo)
            all_data[(author, repo)] = prs
            print(f"  {author} -> {repo}: {len(prs)} PRs")
        except Exception as e:
            print(f"  {author} -> {repo}: query failed - {e}")
            all_data[(author, repo)] = []

    # ── 创建 Word 文档 ─────────────────────────────────
    doc = Document()

    # 页面设置
    section = doc.sections[0]
    section.page_width = Cm(29.7)  # A4 横向
    section.page_height = Cm(21)
    section.orientation = 1  # landscape

    # 标题
    title = doc.add_heading("DEEPCODE PR 贡献追踪报告", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    update_time = doc.add_paragraph(
        f"最后更新: {datetime.datetime.now():%Y年%m月%d日 %H:%M}  |  "
        f"上游仓库: lessweb/deepcode-cli  |  自动生成，请勿手动编辑"
    )
    update_time.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # ── 一、总体概览 ─────────────────────────────────
    doc.add_heading("一、总体概览", level=2)

    # 计算统计
    hq_upstream = all_data[("hqwlkj", "lessweb/deepcode-cli")]
    rm_upstream = all_data[("raymondginger2018-sudo", "lessweb/deepcode-cli")]
    rm_hkuds = all_data[("raymondginger2018-sudo", "HKUDS/DeepCode")]
    rm_self1 = all_data[("raymondginger2018-sudo", "raymondginger2018-sudo/deepcode-cli")]
    rm_self2 = all_data[("raymondginger2018-sudo", "raymondginger2018-sudo/DeepCode")]
    hq_self = all_data[("hqwlkj", "hqwlkj/deepcode-cli")]

    def count_merged(prs): return sum(1 for p in prs if p.get("pull_request", {}).get("merged_at"))
    def count_open(prs): return sum(1 for p in prs if p["state"] == "open")
    def count_closed_unmerged(prs):
        return sum(1 for p in prs if p["state"] == "closed" and not p.get("pull_request", {}).get("merged_at"))

    rows = [
        ["Fork / 仓库", "作者", "目标仓库", "PR 总数", "已合并", "关闭(未合并)", "开放中", "合并率"],
        ["Fork 1", "hqwlkj", "lessweb/deepcode-cli",
         str(len(hq_upstream)), str(count_merged(hq_upstream)),
         str(count_closed_unmerged(hq_upstream)), str(count_open(hq_upstream)),
         f"{count_merged(hq_upstream) * 100 // max(len(hq_upstream), 1)}%"],
        ["Fork 2", "raymondginger2018-sudo", "lessweb/deepcode-cli",
         str(len(rm_upstream)), str(count_merged(rm_upstream)),
         str(count_closed_unmerged(rm_upstream)), str(count_open(rm_upstream)),
         f"{count_merged(rm_upstream) * 100 // max(len(rm_upstream), 1)}%"],
        ["交叉贡献", "raymondginger2018-sudo", "HKUDS/DeepCode",
         str(len(rm_hkuds)), str(count_merged(rm_hkuds)),
         str(count_closed_unmerged(rm_hkuds)), str(count_open(rm_hkuds)),
         f"{count_merged(rm_hkuds) * 100 // max(len(rm_hkuds), 1)}%"],
        ["自合并", "raymondginger2018-sudo", "deepcode-cli (self)",
         str(len(rm_self1)), str(count_merged(rm_self1)),
         str(count_closed_unmerged(rm_self1)), str(count_open(rm_self1)), "—"],
        ["自合并", "raymondginger2018-sudo", "DeepCode (self)",
         str(len(rm_self2)), str(count_merged(rm_self2)),
         str(count_closed_unmerged(rm_self2)), str(count_open(rm_self2)), "—"],
    ]

    total_prs = sum(len(v) for v in all_data.values())
    total_merged = sum(count_merged(v) for v in all_data.values())
    total_open = sum(count_open(v) for v in all_data.values())
    total_closed_um = total_prs - total_merged - total_open
    rows.append(["总计", "—", "—", str(total_prs), str(total_merged),
                 str(total_closed_um), str(total_open),
                 f"{total_merged * 100 // max(total_prs, 1)}%"])

    _add_table(doc, rows)

    # ── 二、Fork 1 详细清单 ───────────────────────────
    doc.add_heading(f"二、Fork 1: hqwlkj/deepcode-cli  ({len(hq_upstream)} PRs)", level=2)
    _add_pr_table(doc, hq_upstream)

    # ── 三、Fork 2 详细清单 ───────────────────────────
    doc.add_heading(f"三、Fork 2: raymondginger2018-sudo/deepcode-cli  ({len(rm_upstream)} PRs)", level=2)
    _add_pr_table(doc, rm_upstream)

    # ── 四、交叉贡献 ─────────────────────────────────
    doc.add_heading(f"四、交叉贡献 — HKUDS/DeepCode  ({len(rm_hkuds)} PRs)", level=2)
    _add_pr_table(doc, rm_hkuds)

    # ── 五、自合并 PR ────────────────────────────────
    doc.add_heading("五、自合并 PR（Fork 内部）", level=2)
    # deepcode-cli self
    if rm_self1:
        doc.add_paragraph("raymondginger2018-sudo/deepcode-cli (内部自合并)", style="Normal")
        _add_pr_table(doc, rm_self1)
    if rm_self2:
        doc.add_paragraph("raymondginger2018-sudo/DeepCode (内部自合并)", style="Normal")
        _add_pr_table(doc, rm_self2)

    # ── 总结 ────────────────────────────────────────
    doc.add_heading("六、总结", level=2)
    hq_merged = count_merged(hq_upstream)
    summary = (
        f"截至 {datetime.datetime.now():%Y年%m月%d日}，"
        f"两个 Fork 总共向上游 lessweb/deepcode-cli 发出了 "
        f"{len(hq_upstream) + len(rm_upstream)} 个 PR，"
        f"其中 {hq_merged} 个已合并。"
        f"涵盖交叉贡献和自合并 PR 后，总计 {total_prs} 个 PR，"
        f"{total_merged} 个已合并，合并率 {total_merged * 100 // max(total_prs, 1)}%。"
    )
    doc.add_paragraph(summary)

    # 保存
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUTPUT_PATH))
    # 更新节流时间戳
    throttle_file.write_text(str(time.time()))
    print(f"  [OK] Document saved: {OUTPUT_PATH}")
    print(f"     Total {total_prs} PRs, {total_merged} merged")


def _add_table(doc, rows, bold_header=True):
    """添加表格到文档"""
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Light Grid Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, row_data in enumerate(rows):
        row = table.rows[i]
        for j, cell_text in enumerate(row_data):
            cell = row.cells[j]
            cell.text = cell_text
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in paragraph.runs:
                    run.font.size = Pt(9)
                    if i == 0 and bold_header:
                        run.bold = True
    doc.add_paragraph()  # spacing


def _add_pr_table(doc, prs):
    """添加 PR 清单表格"""
    if not prs:
        doc.add_paragraph("(暂无)")
        return
    rows = [["状态", "PR #", "标题", "日期"]]
    for p in prs:
        rows.append([
            classify(p),
            str(p["number"]),
            p["title"][:100] + ("..." if len(p["title"]) > 100 else ""),
            p["created_at"][:10],
        ])
    _add_table(doc, rows)


# ── 入口 ────────────────────────────────────────────────
if __name__ == "__main__":
    generate_report()
