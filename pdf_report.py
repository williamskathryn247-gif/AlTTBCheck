"""
pdf_report.py — Generate PDF compliance report using ReportLab.
"""
import os
from io import BytesIO
from datetime import datetime
from typing import List, Dict

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, HRFlowable, PageBreak)

BLUE    = colors.HexColor("#1F3864")
BLUE_L  = colors.HexColor("#D6E4F7")
GREEN   = colors.HexColor("#375623")
GREEN_L = colors.HexColor("#C6EFCE")
RED     = colors.HexColor("#9C0006")
RED_L   = colors.HexColor("#FFCCCC")
AMBER_L = colors.HexColor("#FFF2CC")
GRAY    = colors.HexColor("#595959")
GRAY_L  = colors.HexColor("#F4F4F4")

FIELD_LABELS = {
    "brand_name":        "Brand Name",
    "class_type":        "Class/Type",
    "alcohol_content":   "Alcohol Content",
    "net_contents":      "Net Contents",
    "producer_address":  "Producer/Bottler",
    "country_of_origin": "Country of Origin",
    "health_warning":    "Health Warning",
}

STATUS_COLORS = {
    "match":               GREEN_L,
    "mismatch":            RED_L,
    "missing_application": AMBER_L,
    "missing_label":       AMBER_L,
    "missing_both":        RED_L,
    "error":               GRAY_L,
}

def generate_pdf_report(batch_id: str, results: List[Dict], summary: Dict) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", fontSize=18, textColor=BLUE,
                                 fontName="Helvetica-Bold", spaceAfter=4)
    sub_style   = ParagraphStyle("sub", fontSize=10, textColor=GRAY,
                                 fontName="Helvetica", spaceAfter=12)
    h2_style    = ParagraphStyle("h2", fontSize=13, textColor=BLUE,
                                 fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=6)
    body_style  = ParagraphStyle("body", fontSize=9, fontName="Helvetica",
                                 textColor=colors.black, leading=13)
    disc_style  = ParagraphStyle("disc", fontSize=8, textColor=RED,
                                 fontName="Helvetica", leading=11)

    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph("Alcohol Label Compliance Report", title_style))
    story.append(Paragraph(
        f"Batch ID: {batch_id} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        sub_style))
    story.append(HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=12))

    # ── Summary table ─────────────────────────────────────────────────────────
    story.append(Paragraph("Summary", h2_style))
    total    = summary.get("total_pairs", 0)
    matched  = summary.get("matched", 0)
    mismatch = summary.get("mismatched", 0)
    errs     = summary.get("errors", 0)
    pct      = summary.get("pass_rate", 0)

    sum_data = [
        ["Total Pairs", "Matched", "Mismatched", "Errors", "Pass Rate"],
        [str(total), str(matched), str(mismatch), str(errs), f"{pct:.1f}%"],
    ]
    sum_table = Table(sum_data, colWidths=[1.3*inch]*5)
    sum_table.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0), BLUE),
        ("TEXTCOLOR",    (0,0), (-1,0), colors.white),
        ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,-1), 10),
        ("ALIGN",        (0,0), (-1,-1), "CENTER"),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
        ("ROWHEIGHT",    (0,0), (-1,-1), 22),
        ("BACKGROUND",   (0,1), (-1,1), BLUE_L),
        ("GRID",         (0,0), (-1,-1), 0.5, colors.white),
    ]))
    story.append(sum_table)
    story.append(Spacer(1, 16))

    # ── Results per pair ──────────────────────────────────────────────────────
    story.append(Paragraph("Compliance Results by Reference", h2_style))

    for res in results:
        ref     = res.get("ref", f"Pair {res.get('pair_index','?')+1}")
        overall = res.get("overall_status", "error")
        conf    = res.get("confidence_score", 0)
        disc    = res.get("discrepancies", [])
        if isinstance(disc, str):
            try: disc = __import__("json").loads(disc)
            except: disc = [disc]

        ov_color = GREEN_L if overall == "match" else (RED_L if overall == "mismatch" else GRAY_L)
        ov_text  = "✓ MATCH" if overall == "match" else ("✗ MISMATCH" if overall == "mismatch" else "ERROR")
        ov_fg    = GREEN if overall == "match" else (RED if overall == "mismatch" else GRAY)

        # Pair header row
        hdr_data  = [[ref, ov_text, f"Score: {conf:.0%}"]]
        hdr_table = Table(hdr_data, colWidths=[3*inch, 2*inch, 1.65*inch])
        hdr_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), ov_color),
            ("TEXTCOLOR",  (1,0), (1,0),   ov_fg),
            ("FONTNAME",   (0,0), (-1,-1), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 10),
            ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
            ("ROWHEIGHT",  (0,0), (-1,-1), 20),
            ("LEFTPADDING",(0,0), (-1,-1), 8),
            ("BOX",        (0,0), (-1,-1), 0.5, GRAY),
        ]))
        story.append(hdr_table)

        # Field status grid
        fields  = res.get("fields", {})
        app_fld = res.get("application_fields", {})
        lbl_fld = res.get("label_fields", {})

        field_rows = [["Field", "Application Value", "Label Value", "Status"]]
        for fname, flabel in FIELD_LABELS.items():
            status  = fields.get(fname, "error")
            app_v   = str(app_fld.get(fname) or "—")
            lbl_v   = str(lbl_fld.get(fname) or "—")
            status_label = status.replace("_", " ").title()
            field_rows.append([flabel, app_v[:40], lbl_v[:40], status_label])

        col_w = [1.3*inch, 2.1*inch, 2.1*inch, 1.15*inch]
        ftable = Table(field_rows, colWidths=col_w)
        ts = [
            ("BACKGROUND",  (0,0), (-1,0),  BLUE),
            ("TEXTCOLOR",   (0,0), (-1,0),  colors.white),
            ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
            ("FONTSIZE",    (0,0), (-1,-1), 8),
            ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
            ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
            ("ROWHEIGHT",   (0,0), (-1,-1), 16),
            ("LEFTPADDING", (0,0), (-1,-1), 5),
            ("GRID",        (0,0), (-1,-1), 0.3, colors.lightgrey),
        ]
        for row_i, (fname, _) in enumerate(FIELD_LABELS.items(), start=1):
            status = fields.get(fname, "error")
            bg     = STATUS_COLORS.get(status, GRAY_L)
            ts.append(("BACKGROUND", (3, row_i), (3, row_i), bg))
            ts.append(("BACKGROUND", (0, row_i), (0, row_i), GRAY_L))
        ftable.setStyle(TableStyle(ts))
        story.append(ftable)

        # Discrepancies
        if disc:
            story.append(Spacer(1, 4))
            for d in disc:
                story.append(Paragraph(f"• {d}", disc_style))

        story.append(Spacer(1, 10))

    doc.build(story)
    return buf.getvalue()
