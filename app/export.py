from __future__ import annotations

import io
from datetime import datetime
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import Itinerary


def _money(value: float) -> str:
    return f"{value:,.0f} 元"


def _item_name(item) -> str:
    return f"【必去】{item.name}" if item.must_visit else item.name


def _weather_text(day) -> str:
    return (
        f"{day.date.isoformat()} · {day.condition} · {day.low}~{day.high}℃"
        f" · 降水概率 {day.rain_probability}%"
    )


def _budget_status_text(itinerary: Itinerary) -> str:
    budget = itinerary.budget
    if budget.budget_status == "ok":
        limit_text = f"（预算上限 {_money(budget.limit_total)}）" if budget.limit_total is not None else ""
        return f"预算内，剩余 {_money(budget.remaining or 0)}{limit_text}"
    if budget.budget_status == "over_budget":
        tolerance_text = (
            f"{_money(budget.tolerance_amount)}容差内"
            if budget.within_tolerance
            else f"超过 {_money(budget.tolerance_amount)}容差"
        )
        limit_text = f"预算上限 {_money(budget.limit_total)}；" if budget.limit_total is not None else ""
        return f"超出预算 {_money(budget.over_budget_amount)}（{limit_text}{tolerance_text}）"
    return "未设置预算上限"


def _item_lines(itinerary: Itinerary) -> list[str]:
    lines: list[str] = []
    for day in itinerary.days:
        lines.append(f"第 {day.day_index} 天 · {day.date.isoformat()} · {day.theme}")
        lines.append(_weather_text(day.weather))
        for item in day.items:
            cost = f"，{_money(item.cost)}（{ {'per_person': '每人', 'per_room': '每间', 'per_group': '每组'}[item.cost_basis] }）" if item.cost else ""
            lines.append(f"  {item.start_time}-{item.end_time}  {_item_name(item)}{cost}")
            if item.note:
                lines.append(f"    说明：{item.note}")
        for leg in day.legs:
            if leg.distance_km > 0:
                lines.append(f"  交通：{leg.mode} {leg.from_place} → {leg.to_place}，约 {leg.minutes} 分钟 / {leg.distance_km} km")
        for note in day.notes:
            lines.append(f"  提示：{note}")
        lines.append("")
    return lines


def build_markdown(itinerary: Itinerary) -> str:
    lines = [
        f"# 行程规划：{itinerary.destination} {itinerary.days_count} 天",
        "",
        f"- 日期：{itinerary.start_date.isoformat()} 至 {itinerary.end_date.isoformat()}",
        f"- 人数：{itinerary.travelers} 人",
        f"- 出发地：{itinerary.origin or '未提供'}",
        f"- 数据模式：{itinerary.data_mode_label}",
        "",
        f"> {itinerary.summary}",
        "",
        "## 预算",
        "",
        "| 项目 | 金额 | 说明 |",
        "| --- | --- | --- |",
    ]
    for line in itinerary.budget.lines:
        lines.append(f"| {line.label} | {_money(line.amount)} | {line.detail} |")
    lines.extend(
        [
            f"| **合计** | **{_money(itinerary.budget.total)}** | 人均 {_money(itinerary.budget.per_person)} |",
            "",
            f"预算状态：{_budget_status_text(itinerary)}",
            "",
        ]
    )
    if itinerary.budget.adjustments:
        lines.extend(["**预算适配记录**", ""])
        lines.extend(f"- {item}" for item in itinerary.budget.adjustments)
        lines.append("")
    lines.extend(["## 每日行程", ""])
    lines.extend(_item_lines(itinerary))
    if itinerary.tips:
        lines.extend(["## 出行提示", ""])
        lines.extend(f"- {tip}" for tip in itinerary.tips)
        lines.append("")
    if itinerary.warnings:
        lines.extend(["## 风险与待确认", ""])
        lines.extend(f"- {warning}" for warning in itinerary.warnings)
        lines.append("")
    lines.extend(["## 数据来源", ""])
    lines.extend(f"- {source.title}：{source.detail}" for source in itinerary.sources)
    lines.append("")
    if itinerary.assumptions:
        lines.extend(["## 假设", ""])
        lines.extend(f"- {assumption}" for assumption in itinerary.assumptions)
    lines.extend(["", f"生成时间：{itinerary.created_at.strftime('%Y-%m-%d %H:%M:%S')}"])
    return "\n".join(lines)


def build_docx(itinerary: Itinerary) -> bytes:
    document = Document()
    section = document.sections[0]
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)

    normal = document.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")

    title = document.add_heading(f"{itinerary.destination} {itinerary.days_count} 天行程规划", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in title.runs:
        run.font.name = "Microsoft YaHei"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        run.font.color.rgb = RGBColor(0x1F, 0x4D, 0x4A)

    meta = document.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run(
        f"{itinerary.start_date.isoformat()} 至 {itinerary.end_date.isoformat()} · "
        f"{itinerary.travelers} 人 · 出发地：{itinerary.origin or '未提供'}"
    ).italic = True

    document.add_paragraph(itinerary.summary)

    if itinerary.demo_mode:
        note = document.add_paragraph()
        run = note.add_run(f"数据模式：{itinerary.data_mode_label}。示例数据不代表实时查询结果。")
        run.font.color.rgb = RGBColor(0xA8, 0x4A, 0x1F)
        run.bold = True

    document.add_heading("预算", level=1)
    table = document.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    header = table.rows[0].cells
    header[0].text, header[1].text, header[2].text = "项目", "金额", "说明"
    for line in itinerary.budget.lines:
        cells = table.add_row().cells
        cells[0].text = line.label
        cells[1].text = _money(line.amount)
        cells[2].text = line.detail
    total_row = table.add_row().cells
    total_row[0].text = "合计"
    total_row[1].text = _money(itinerary.budget.total)
    total_row[2].text = f"人均 {_money(itinerary.budget.per_person)}；{_budget_status_text(itinerary)}"

    if itinerary.budget.suggestions:
        document.add_heading("预算建议", level=2)
        for suggestion in itinerary.budget.suggestions:
            document.add_paragraph(suggestion, style="List Bullet")

    if itinerary.budget.adjustments:
        document.add_heading("预算适配记录", level=2)
        for item in itinerary.budget.adjustments:
            document.add_paragraph(item, style="List Bullet")

    document.add_heading("每日行程", level=1)
    for day in itinerary.days:
        document.add_heading(f"第 {day.day_index} 天 · {day.date.isoformat()} · {day.theme}", level=2)
        document.add_paragraph(_weather_text(day.weather))
        for item in day.items:
            line = document.add_paragraph(style="List Bullet")
            line.add_run(f"{item.start_time}-{item.end_time} ").bold = True
            line.add_run(_item_name(item))
            if item.cost:
                line.add_run(f"（{_money(item.cost)}）")
            if item.note:
                line.add_run(f" — {item.note}")
        for leg in day.legs:
            if leg.distance_km > 0:
                document.add_paragraph(
                    f"交通：{leg.mode} {leg.from_place} → {leg.to_place}，约 {leg.minutes} 分钟 / {leg.distance_km} km",
                    style="List Bullet 2",
                )
        for note in day.notes:
            document.add_paragraph(f"提示：{note}", style="List Bullet 2")

    if itinerary.tips:
        document.add_heading("出行提示", level=1)
        for tip in itinerary.tips:
            document.add_paragraph(tip, style="List Bullet")

    if itinerary.warnings:
        document.add_heading("风险与待确认", level=1)
        for warning in itinerary.warnings:
            document.add_paragraph(warning, style="List Bullet")

    document.add_heading("数据来源与边界", level=1)
    for source in itinerary.sources:
        document.add_paragraph(f"{source.title}：{source.detail}", style="List Bullet")

    if itinerary.assumptions:
        document.add_heading("假设", level=1)
        for assumption in itinerary.assumptions:
            document.add_paragraph(assumption, style="List Bullet")

    footer = document.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run(f"由行迹 AI 旅行助手生成 · {itinerary.created_at.strftime('%Y-%m-%d %H:%M')}").font.size = Pt(8)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _pdf_styles() -> dict:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("TitleCN", parent=base["Title"], fontName="STSong-Light", fontSize=20, leading=28, alignment=TA_CENTER, textColor=colors.HexColor("#1F4D4A")),
        "meta": ParagraphStyle("MetaCN", parent=base["Normal"], fontName="STSong-Light", fontSize=9, leading=14, alignment=TA_CENTER, textColor=colors.HexColor("#6B7280")),
        "h1": ParagraphStyle("H1CN", parent=base["Heading1"], fontName="STSong-Light", fontSize=14, leading=20, spaceBefore=10, spaceAfter=5, textColor=colors.HexColor("#1F4D4A")),
        "h2": ParagraphStyle("H2CN", parent=base["Heading2"], fontName="STSong-Light", fontSize=11.5, leading=17, spaceBefore=8, spaceAfter=3, textColor=colors.HexColor("#2B5F5A")),
        "body": ParagraphStyle("BodyCN", parent=base["Normal"], fontName="STSong-Light", fontSize=9.5, leading=15, alignment=TA_LEFT),
        "small": ParagraphStyle("SmallCN", parent=base["Normal"], fontName="STSong-Light", fontSize=8.5, leading=13, textColor=colors.HexColor("#4B5563")),
    }


def build_pdf(itinerary: Itinerary) -> bytes:
    styles = _pdf_styles()
    buffer = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("STSong-Light", 8)
        canvas.setFillColor(colors.HexColor("#9CA3AF"))
        canvas.drawString(20 * mm, 12 * mm, f"行迹 AI 旅行助手 · {itinerary.trip_id}")
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"第 {canvas.getPageNumber()} 页")
        canvas.restoreState()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=f"{itinerary.destination}行程规划",
        author="行迹 AI 旅行助手",
    )

    story = [
        Paragraph(escape(f"{itinerary.destination} {itinerary.days_count} 天行程规划"), styles["title"]),
        Paragraph(
            escape(
                f"{itinerary.start_date.isoformat()} 至 {itinerary.end_date.isoformat()} · "
                f"{itinerary.travelers} 人 · 出发地：{itinerary.origin or '未提供'}"
            ),
            styles["meta"],
        ),
        Spacer(1, 8),
        Paragraph(escape(itinerary.summary), styles["body"]),
    ]
    if itinerary.demo_mode:
        story.append(
            Paragraph(
                escape(f"数据模式：{itinerary.data_mode_label}。示例数据不代表实时查询结果。"),
                ParagraphStyle("WarnCN", parent=styles["small"], textColor=colors.HexColor("#A84A1F")),
            )
        )

    story.append(Paragraph("预算", styles["h1"]))
    table_data = [["项目", "金额", "说明"]]
    for line in itinerary.budget.lines:
        table_data.append([line.label, _money(line.amount), line.detail])
    table_data.append(["合计", _money(itinerary.budget.total), f"人均 {_money(itinerary.budget.per_person)}；{_budget_status_text(itinerary)}"])
    table = Table(table_data, colWidths=[30 * mm, 30 * mm, 110 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F1EF")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1F4D4A")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D1D5DB")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F9FAF9")),
            ]
        )
    )
    story.append(table)
    if itinerary.budget.suggestions:
        story.append(Paragraph("预算建议", styles["h2"]))
        for suggestion in itinerary.budget.suggestions:
            story.append(Paragraph(f"• {escape(suggestion)}", styles["small"]))

    if itinerary.budget.adjustments:
        story.append(Paragraph("预算适配记录", styles["h2"]))
        for item in itinerary.budget.adjustments:
            story.append(Paragraph(f"• {escape(item)}", styles["small"]))

    story.append(Paragraph("每日行程", styles["h1"]))
    for day in itinerary.days:
        story.append(Paragraph(escape(f"第 {day.day_index} 天 · {day.date.isoformat()} · {day.theme}"), styles["h2"]))
        story.append(Paragraph(escape(_weather_text(day.weather)), styles["small"]))
        for item in day.items:
            cost = f"（{_money(item.cost)}）" if item.cost else ""
            text = f"{item.start_time}-{item.end_time} <b>{escape(_item_name(item))}</b>{escape(cost)}"
            if item.note:
                text += f" — {escape(item.note)}"
            story.append(Paragraph(text, styles["body"]))
        for leg in day.legs:
            if leg.distance_km > 0:
                story.append(
                    Paragraph(
                        escape(f"交通：{leg.mode} {leg.from_place} → {leg.to_place}，约 {leg.minutes} 分钟 / {leg.distance_km} km"),
                        styles["small"],
                    )
                )
        for note in day.notes:
            story.append(Paragraph(escape(f"提示：{note}"), styles["small"]))
        story.append(Spacer(1, 4))

    if itinerary.tips:
        story.append(Paragraph("出行提示", styles["h1"]))
        for tip in itinerary.tips:
            story.append(Paragraph(f"• {escape(tip)}", styles["body"]))
    if itinerary.warnings:
        story.append(Paragraph("风险与待确认", styles["h1"]))
        for warning in itinerary.warnings:
            story.append(Paragraph(f"• {escape(warning)}", styles["small"]))
    story.append(Paragraph("数据来源与边界", styles["h1"]))
    for source in itinerary.sources:
        story.append(Paragraph(f"• {escape(source.title)}：{escape(source.detail)}", styles["small"]))
    if itinerary.assumptions:
        story.append(Paragraph("假设", styles["h1"]))
        for assumption in itinerary.assumptions:
            story.append(Paragraph(f"• {escape(assumption)}", styles["small"]))

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _rtf_escape(text: str) -> str:
    out: list[str] = []
    for char in text:
        code = ord(char)
        if char == "\\":
            out.append("\\\\")
        elif char == "{":
            out.append("\\{")
        elif char == "}":
            out.append("\\}")
        elif char == "\n":
            out.append("\\par\n")
        elif code < 128:
            out.append(char)
        else:
            signed = code if code < 32768 else code - 65536
            out.append(f"\\u{signed}?")
    return "".join(out)


def build_doc_rtf(itinerary: Itinerary) -> bytes:
    """生成 Word 可直接打开的 .doc（RTF 容器），兼容中文。"""
    lines: list[str] = [
        f"{itinerary.destination} {itinerary.days_count} 天行程规划",
        f"{itinerary.start_date.isoformat()} 至 {itinerary.end_date.isoformat()} · {itinerary.travelers} 人 · 出发地：{itinerary.origin or '未提供'}",
        itinerary.summary,
        "",
        f"数据模式：{itinerary.data_mode_label}",
        "",
        "【预算】",
    ]
    for line in itinerary.budget.lines:
        lines.append(f"{line.label}：{_money(line.amount)}　{line.detail}")
    lines.append(f"合计：{_money(itinerary.budget.total)}；人均 {_money(itinerary.budget.per_person)}；{_budget_status_text(itinerary)}")
    if itinerary.budget.suggestions:
        lines.append("预算建议：")
        lines.extend(f"- {item}" for item in itinerary.budget.suggestions)
    if itinerary.budget.adjustments:
        lines.append("预算适配记录：")
        lines.extend(f"- {item}" for item in itinerary.budget.adjustments)
    lines.extend(["", "【每日行程】"])
    for day in itinerary.days:
        lines.append("")
        lines.append(f"第 {day.day_index} 天 · {day.date.isoformat()} · {day.theme}")
        lines.append(_weather_text(day.weather))
        for item in day.items:
            lines.append(f"{item.start_time}-{item.end_time} {_item_name(item)}" + (f"（{_money(item.cost)}）" if item.cost else ""))
            if item.note:
                lines.append(f"    说明：{item.note}")
        for leg in day.legs:
            if leg.distance_km > 0:
                lines.append(f"    交通：{leg.mode} {leg.from_place} → {leg.to_place}，约 {leg.minutes} 分钟 / {leg.distance_km} km")
        for note in day.notes:
            lines.append(f"    提示：{note}")
    if itinerary.tips:
        lines.extend(["", "【出行提示】"])
        lines.extend(f"- {tip}" for tip in itinerary.tips)
    if itinerary.warnings:
        lines.extend(["", "【风险与待确认】"])
        lines.extend(f"- {warning}" for warning in itinerary.warnings)
    lines.extend(["", "【数据来源与边界】"])
    lines.extend(f"- {source.title}：{source.detail}" for source in itinerary.sources)
    if itinerary.assumptions:
        lines.extend(["", "【假设】"])
        lines.extend(f"- {item}" for item in itinerary.assumptions)
    lines.extend(["", f"生成时间：{itinerary.created_at.strftime('%Y-%m-%d %H:%M:%S')}"])

    body = r"\par ".join(_rtf_escape(line) for line in lines)
    rtf = (
        r"{\rtf1\ansi\ansicpg936\deff0"
        r"{\fonttbl{\f0\fnil\fcharset134 Microsoft YaHei;}}"
        r"\viewkind4\uc1\pard\f0\fs20 "
        + body
        + r"\par}"
    )
    return rtf.encode("utf-8")
