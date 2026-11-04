"""Excel, CSV, Word and PDF generation from real data."""
from __future__ import annotations

import io
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402  (thread-safe, unlike pyplot)
from django.utils import timezone  # noqa: E402
from django.utils.translation import gettext as _  # noqa: E402
from docx import Document as DocxDocument  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.shared import Cm, Pt, RGBColor  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402
from xml.sax.saxutils import escape  # noqa: E402

from analytics import stats, text as textanalysis  # noqa: E402
from analytics.models import ResearchFinding, ReviewStatus  # noqa: E402
from surveys.models import Participant, Question, SurveyResponse  # noqa: E402

from . import datasets  # noqa: E402

FONT_DIR = Path(__file__).resolve().parent / "fonts"
NAVY = "#102A43"
ROYAL = "#2457A7"
GOLD = "#C6A15B"

for f in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
    font_manager.fontManager.addfont(str(FONT_DIR / f))
matplotlib.rcParams["font.family"] = "DejaVu Sans"
pdfmetrics.registerFont(TTFont("DejaVu", str(FONT_DIR / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu", boldItalic="DejaVu-Bold")


# --------------------------------------------------------------------------- Excel / CSV
def _write_sheet(wb: Workbook, title: str, frame, first: bool = False) -> None:
    ws = wb.active if first else wb.create_sheet()
    ws.title = title[:31]
    if frame is None or frame.empty:
        ws.append([_("No data")])
        return
    headers = [str(c) for c in frame.columns]
    ws.append(headers)
    for row in frame.itertuples(index=False):
        ws.append([None if (isinstance(v, float) and v != v) else v for v in row])
    header_fill = PatternFill("solid", fgColor="102A43")
    for i, cell in enumerate(ws[1], start=1):
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        width = min(60, max(10, len(headers[i - 1]) + 2))
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"


def build_xlsx(questionnaire) -> tuple[bytes, int]:
    ds = stats.load_dataset(questionnaire)
    wb = Workbook()
    _write_sheet(wb, _("Responses (labels)"), datasets.wide_dataset(ds, labels=True), first=True)
    _write_sheet(wb, _("Responses (codes)"), datasets.wide_dataset(ds, labels=False))
    _write_sheet(wb, _("Answers (long)"), datasets.answers_long(ds))
    participants = datasets.wide_dataset(ds, labels=True)
    pcols = [c for c in participants.columns if c.startswith("p_") or c in ("response_id", "submitted_at", "consent")]
    _write_sheet(wb, _("Participants"), participants[pcols] if not participants.empty else participants)
    _write_sheet(wb, _("Evaluations"), datasets.evaluations_frame(questionnaire))
    _write_sheet(wb, _("Statistics"), datasets.statistics_frame(ds))
    _write_sheet(wb, _("Codebook"), datasets.codebook_frame(ds))
    meta = wb.create_sheet(_("About")[:31])
    for row in (
        (_("Questionnaire"), questionnaire.title),
        (_("Exported at"), timezone.localtime().strftime("%Y-%m-%d %H:%M")),
        (_("Completed responses (N)"), ds.n),
        (_("Source"), "ResearchSurvey / PostgreSQL"),
    ):
        meta.append([str(row[0]), datasets.safe_cell(row[1])])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), ds.n


def build_csv(questionnaire, *, labels: bool = True) -> tuple[bytes, int]:
    ds = stats.load_dataset(questionnaire)
    frame = datasets.wide_dataset(ds, labels=labels)
    return frame.to_csv(index=False).encode("utf-8-sig"), ds.n


def build_responses_xlsx(responses: list[SurveyResponse]) -> bytes:
    import pandas as pd

    wb = Workbook()
    records = []
    for r in responses:
        for item in datasets.per_response_answers(r):
            q = item["question"]
            records.append(
                {
                    "response_id": r.reference,
                    "number": q.number,
                    "code": q.code,
                    "question": q.text,
                    "answer": "; ".join(a.display_value for a in item["answers"]) or None,
                }
            )
    _write_sheet(wb, _("Responses"), pd.DataFrame.from_records(records).map(datasets.safe_cell), first=True)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- charts
def chart_png(chart: dict | None, title: str = "") -> bytes | None:
    if not chart:
        return None
    labels = [textwrap.shorten(str(lbl), 45, placeholder="…") for lbl in chart.get("labels", [])]
    if not labels:
        return None
    ctype = chart.get("type")
    fig_h = max(2.6, 0.38 * len(labels) + 1.2) if chart.get("horizontal") or ctype in ("stacked",) else 3.2
    fig = Figure(figsize=(7.2, fig_h), dpi=150)
    ax = fig.subplots()
    palette = [NAVY, ROYAL, GOLD, "#16866A", "#D69E36", "#64748B", "#7C93C3", "#A58A4F", "#3C6E71", "#9A3B3B"]
    if ctype == "stacked" or ctype == "grouped":
        datasets_ = chart.get("datasets", [])
        import numpy as np

        y = np.arange(len(labels))
        if ctype == "stacked":
            left = np.zeros(len(labels))
            for i, d in enumerate(datasets_):
                ax.barh(y, d["values"], left=left, color=palette[i % len(palette)], label=textwrap.shorten(d["label"], 30))
                left += np.asarray(d["values"], dtype=float)
            ax.set_yticks(y, labels)
            ax.invert_yaxis()
        else:
            width = 0.8 / max(1, len(datasets_))
            for i, d in enumerate(datasets_):
                ax.bar(y + i * width, d["values"], width, color=palette[i % len(palette)], label=textwrap.shorten(d["label"], 30))
            ax.set_xticks(y + width * (len(datasets_) - 1) / 2, labels, rotation=20, ha="right")
        ax.legend(fontsize=7, frameon=False)
    elif ctype == "pie":
        values = chart.get("values", [])
        if sum(values) == 0:
            return None
        ax.pie(values, labels=labels, colors=palette[: len(values)], autopct="%1.0f%%", textprops={"fontsize": 7})
        ax.axis("equal")
    elif ctype == "line":
        ax.plot(labels, chart.get("values", []), color=ROYAL, marker="o")
        ax.tick_params(axis="x", rotation=30)
    elif chart.get("horizontal"):
        ax.barh(labels, chart.get("values", []), color=ROYAL)
        ax.invert_yaxis()
    else:
        ax.bar(labels, chart.get("values", []), color=ROYAL)
        ax.tick_params(axis="x", rotation=20 if len(labels) > 5 else 0)
    ax.tick_params(labelsize=7)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    if title:
        ax.set_title(textwrap.shorten(title, 90), fontsize=8, color=NAVY)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


# --------------------------------------------------------------------------- report model
@dataclass
class Block:
    kind: str  # h1 | h2 | h3 | p | small | table | image | pagebreak
    text: str = ""
    rows: list[list[Any]] = field(default_factory=list)
    image: bytes | None = None


def _paras(text: str) -> list[Block]:
    return [Block("p", line.strip()) for line in (text or "").split("\n") if line.strip()]


def _question_blocks(ds: stats.Dataset, q: Question, include_chart: bool) -> list[Block]:
    s = stats.question_summary(ds, q)
    out = [Block("h3", q.display_label), Block("small", _("N = %(N)s, answered n = %(n)s, missing = %(m)s") % {"N": s["N"], "n": s["n"], "m": s["missing"]})]
    if s.get("rows"):
        rows = [[_("Answer"), _("Count"), "%"]] + [[r["option"], r["count"], r["pct"]] for r in s["rows"]]
        out.append(Block("table", rows=rows))
    st = s.get("stats") or {}
    if "mean" in st:
        out.append(
            Block(
                "table",
                rows=[
                    [_("n"), _("Mean"), _("Median"), _("Mode"), _("SD"), _("Variance"), _("Min"), _("Max")],
                    [st["n"], st["mean"], st["median"], ", ".join(str(m) for m in st.get("mode") or []) or "—", st.get("std") or "—", st.get("variance") or "—", st["min"], st["max"]],
                ],
            )
        )
    if s.get("matrix"):
        m = s["matrix"]
        out.append(Block("table", rows=[[""] + m["columns"]] + [[r["row"]] + r["counts"] for r in m["rows"]]))
    if s.get("ranking"):
        out.append(Block("table", rows=[[_("Option"), _("n"), _("Mean rank"), _("Ranked first")]] + [[r["option"], r["n"], r["mean_rank"], r["first"]] for r in s["ranking"]]))
    if q.is_text and s.get("texts"):
        analysis = textanalysis.analyse(s["texts"])
        terms = ", ".join(f"{t['term']} ({t['count']})" for t in analysis["terms"][:12])
        if terms:
            out.append(Block("p", _("Frequent terms: %(t)s") % {"t": terms}))
        for g in analysis["groups"][:3]:
            out.append(Block("p", _("Recurring theme (%(k)s answers): %(l)s") % {"k": g["size"], "l": g["label"]}))
    if include_chart:
        png = chart_png(s.get("chart"))
        if png:
            out.append(Block("image", image=png))
    return out


def report_blocks(report) -> list[Block]:
    qn = report.questionnaire
    ds = stats.load_dataset(qn)
    ov = stats.overview(ds)
    blocks: list[Block] = [Block("h1", report.title)]
    if qn.author:
        blocks.append(Block("small", qn.author))
    blocks.append(Block("small", _("Generated %(d)s from %(n)s completed responses.") % {"d": timezone.localtime().strftime("%Y-%m-%d"), "n": ds.n}))

    blocks.append(Block("h2", "1. " + _("Research objectives")))
    blocks += _paras(report.objectives or qn.objective) or [Block("p", "—")]
    blocks.append(Block("h2", "2. " + _("Methodology")))
    blocks += _paras(report.methodology)
    type_counts: dict[str, int] = {}
    for q in ds.questions:
        type_counts[q.get_qtype_display()] = type_counts.get(q.get_qtype_display(), 0) + 1
    blocks.append(
        Block(
            "p",
            _("Instrument: %(q)s questions in %(s)s sections (%(t)s). Data were collected online with ResearchSurvey; statistics were computed from stored responses.")
            % {"q": len(ds.questions), "s": qn.sections.count(), "t": ", ".join(f"{k}: {v}" for k, v in type_counts.items())},
        )
    )
    blocks.append(Block("h2", "3. " + _("Participants and sample")))
    blocks += _paras(report.participants_text)
    blocks.append(
        Block(
            "table",
            rows=[
                [_("Started"), _("Completed (N)"), _("Incomplete"), _("Completion rate, %"), _("Median time, min")],
                [ov["started"], ov["N"], ov["incomplete"], ov["completion_rate"] or "—", ov["median_minutes"] or "—"],
            ],
        )
    )
    for name, label in stats.PARTICIPANT_LABELS.items():
        values = [getattr(r.participant, name) for r in ds.responses.values() if getattr(r.participant, name)]
        if values:
            from collections import Counter

            c = Counter(values)
            blocks.append(Block("h3", _(label)))
            blocks.append(Block("table", rows=[[_(label), _("Count"), "%"]] + [[k, v, round(100 * v / ds.n, 1)] for k, v in c.most_common()]))

    blocks.append(Block("h2", "4. " + _("Statistical results")))
    sections = stats.section_comparison(ds)
    if sections["rows"]:
        blocks.append(Block("p", _("Section means (Likert items, original scale):")))
        blocks.append(
            Block("table", rows=[[_("Section"), "n", _("Mean"), _("SD"), _("Median")]] + [[r["section"], r.get("n"), r.get("mean"), r.get("std"), r.get("median")] for r in sections["rows"]])
        )
        if report.include_charts:
            png = chart_png(sections["chart"])
            if png:
                blocks.append(Block("image", image=png))
    if report.include_question_analysis:
        blocks.append(Block("h2", "5. " + _("Analysis of individual questions")))
        current_section = None
        for q in ds.questions:
            if q.section_id != current_section:
                current_section = q.section_id
                blocks.append(Block("h2", q.section.title))
            blocks += _question_blocks(ds, q, report.include_charts)

    blocks.append(Block("h2", "6. " + _("Main research findings")))
    blocks += _paras(report.findings)
    approved = ResearchFinding.objects.filter(questionnaire=qn, status=ReviewStatus.APPROVED)
    for f in approved:
        blocks.append(Block("p", f"• {f.text}"))
    if not report.findings and not approved:
        blocks.append(Block("p", "—"))
    for num, label, value in (
        ("7", _("Discussion"), report.discussion),
        ("8", _("Conclusions"), report.conclusions),
        ("9", _("Recommendations"), report.recommendations),
    ):
        blocks.append(Block("h2", f"{num}. {label}"))
        blocks += _paras(value) or [Block("p", "—")]
    return blocks


def response_blocks(response: SurveyResponse, *, include_evaluation: bool = True) -> list[Block]:
    p: Participant = response.participant
    blocks = [Block("h1", response.questionnaire.title), Block("h2", _("Individual response %(id)s") % {"id": response.reference})]
    meta = [[_("Status"), response.get_status_display()], [_("Started"), timezone.localtime(response.started_at).strftime("%Y-%m-%d %H:%M")]]
    if response.submitted_at:
        meta.append([_("Submitted"), timezone.localtime(response.submitted_at).strftime("%Y-%m-%d %H:%M")])
    for name in Participant.STANDARD_FIELDS:
        value = getattr(p, name)
        if value:
            meta.append([str(Participant._meta.get_field(name).verbose_name).capitalize(), value])
    for k, v in (p.extra or {}).items():
        meta.append([k, v])
    blocks.append(Block("table", rows=meta))
    current = None
    for item in datasets.per_response_answers(response):
        if item["section"].pk != current:
            current = item["section"].pk
            blocks.append(Block("h3", item["section"].title))
        value = "; ".join(a.display_value for a in item["answers"]) or "—"
        blocks.append(Block("p", f"{item['question'].display_label}"))
        blocks.append(Block("answer", value))
    evaluation = getattr(response, "evaluation", None) if include_evaluation else None
    if evaluation is not None:
        blocks.append(Block("h2", _("Evaluation (separate from the original answers)")))
        rows = [[_("Criterion"), _("Score"), _("Maximum"), _("Weight")]]
        for s in evaluation.scores.select_related("criterion"):
            rows.append([s.criterion.name, s.score, s.criterion.max_score, s.criterion.weight])
        rows.append([_("Final score, %"), evaluation.final_score if evaluation.final_score is not None else "—", "", ""])
        blocks.append(Block("table", rows=rows))
        if evaluation.comment:
            blocks.append(Block("p", _("Scientific comment: %(c)s") % {"c": evaluation.comment}))
        if evaluation.recommendations:
            blocks.append(Block("p", _("Recommendations: %(c)s") % {"c": evaluation.recommendations}))
    return blocks


# --------------------------------------------------------------------------- Word
def blocks_to_docx(blocks: list[Block]) -> bytes:
    doc = DocxDocument()
    for section in doc.sections:
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(1.5)
        section.top_margin = Cm(2)
        section.bottom_margin = Cm(2)
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    for b in blocks:
        if b.kind == "h1":
            h = doc.add_heading(b.text, level=0)
            h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif b.kind in ("h2", "h3"):
            doc.add_heading(b.text, level=1 if b.kind == "h2" else 2)
        elif b.kind == "small":
            para = doc.add_paragraph()
            run = para.add_run(b.text)
            run.italic = True
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)
        elif b.kind == "answer":
            para = doc.add_paragraph()
            para.paragraph_format.left_indent = Cm(0.8)
            run = para.add_run(b.text)
            run.bold = True
            run.font.color.rgb = RGBColor(0x24, 0x57, 0xA7)
        elif b.kind == "p":
            para = doc.add_paragraph(b.text)
            para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        elif b.kind == "table" and b.rows:
            ncols = max(len(r) for r in b.rows)
            table = doc.add_table(rows=0, cols=ncols)
            table.style = "Table Grid"
            for i, row in enumerate(b.rows):
                cells = table.add_row().cells
                for j in range(ncols):
                    value = row[j] if j < len(row) else ""
                    cells[j].text = "" if value is None else str(value)
                    for para in cells[j].paragraphs:
                        for run in para.runs:
                            run.font.size = Pt(10)
                            run.bold = i == 0
            doc.add_paragraph()
        elif b.kind == "image" and b.image:
            doc.add_picture(io.BytesIO(b.image), width=Cm(15))
        elif b.kind == "pagebreak":
            doc.add_page_break()
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- PDF
def _pdf_styles():
    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=base["Title"], fontName="DejaVu-Bold", fontSize=16, leading=20, textColor=colors.HexColor(NAVY), alignment=TA_CENTER, spaceAfter=10),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="DejaVu-Bold", fontSize=12.5, leading=16, textColor=colors.HexColor(NAVY), spaceBefore=10, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontName="DejaVu-Bold", fontSize=10.5, leading=14, textColor=colors.HexColor(ROYAL), spaceBefore=8, spaceAfter=4),
        "p": ParagraphStyle("p", parent=base["BodyText"], fontName="DejaVu", fontSize=9.5, leading=13.5, spaceAfter=4),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontName="DejaVu", fontSize=8, leading=11, textColor=colors.HexColor("#64748B")),
        "answer": ParagraphStyle("answer", parent=base["BodyText"], fontName="DejaVu-Bold", fontSize=9.5, leading=13, leftIndent=14, textColor=colors.HexColor(ROYAL), spaceAfter=6),
        "cell": ParagraphStyle("cell", parent=base["BodyText"], fontName="DejaVu", fontSize=8, leading=10),
    }


def blocks_to_pdf(blocks: list[Block], title: str = "ResearchSurvey") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2.2 * cm, rightMargin=1.6 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm, title=title, author="ResearchSurvey")
    st = _pdf_styles()
    story: list = []
    for b in blocks:
        if b.kind in ("h1", "h2", "h3", "p", "small", "answer"):
            story.append(Paragraph(escape(b.text).replace("\n", "<br/>"), st[b.kind]))
        elif b.kind == "table" and b.rows:
            ncols = max(len(r) for r in b.rows)
            data = [[Paragraph(escape("" if (j >= len(r) or r[j] is None) else str(r[j])), st["cell"]) for j in range(ncols)] for r in b.rows]
            width = doc.width
            first = min(0.55, 0.25 + 0.06 * ncols) if ncols > 2 else 0.5
            col_widths = [width * first] + [width * (1 - first) / (ncols - 1)] * (ncols - 1) if ncols > 1 else [width]
            t = Table(data, colWidths=col_widths, repeatRows=1)
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            story += [t, Spacer(1, 8)]
        elif b.kind == "image" and b.image:
            img = Image(io.BytesIO(b.image))
            ratio = img.imageHeight / img.imageWidth
            img.drawWidth = min(doc.width, 15 * cm)
            img.drawHeight = img.drawWidth * ratio
            story += [img, Spacer(1, 8)]
        elif b.kind == "pagebreak":
            story.append(PageBreak())

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("DejaVu", 7.5)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(2.2 * cm, 1.1 * cm, f"ResearchSurvey · {title[:90]}")
        canvas.drawRightString(A4[0] - 1.6 * cm, 1.1 * cm, str(document.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
