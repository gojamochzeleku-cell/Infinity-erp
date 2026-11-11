"""
Pure document rendering for the BIDM War Room.

This module:
- Does not query or modify Django models.
- Does not write files or database records.
- Renders PDF, DOCX and XLSX from a prepared payload.
- Keeps tender facts, AI assessments and unverified information distinct.
"""

from io import BytesIO
from xml.sax.saxutils import escape


ARTIFACTS = {
    "TENDER_INTELLIGENCE": {
        "title": "Tender Intelligence Report",
        "formats": ("PDF", "DOCX"),
        "sections": (
            ("Executive Summary", "executive_summary"),
            ("Tender Facts", "tender_facts"),
            ("Lot Analysis", "lot_analysis"),
            ("Commercial Analysis", "commercial_analysis"),
            ("Bid Strategy", "bid_strategy"),
        ),
    },
    "TECHNICAL_COMPLIANCE": {
        "title": "Technical Specification & Compliance Matrix",
        "formats": ("XLSX", "PDF"),
        "sections": (("Technical Requirements", "technical_requirements"),),
    },
    "BRAND_COMPARISON": {
        "title": "Brand & Model Comparison Report",
        "formats": ("PDF", "DOCX", "XLSX"),
        "sections": (("Brand Sourcing", "brand_sourcing"),),
    },
    "SUPPLIER_INTELLIGENCE": {
        "title": "Supplier Intelligence & Evaluation Report",
        "formats": ("PDF", "DOCX", "XLSX"),
        "sections": (("Supplier Strategy", "supplier_strategy"),),
    },
    "COMMERCIAL_ANALYSIS": {
        "title": "Commercial & Landed Cost Analysis",
        "formats": ("XLSX", "PDF"),
        "sections": (("Commercial Analysis", "commercial_analysis"),
                     ("Lot Commercial Data", "lot_analysis")),
    },
    "ELIGIBILITY_RISK": {
        "title": "Eligibility, Risk & Clarification Report",
        "formats": ("PDF", "DOCX"),
        "sections": (
            ("Submission Checklist", "submission_checklist"),
            ("Risk Register", "risk_register"),
            ("Missing Information & Clarifications", "missing_information"),
        ),
    },
    "BID_STRATEGY": {
        "title": "BIDM Bid Strategy & Recommendation",
        "formats": ("PDF", "DOCX"),
        "sections": (
            ("Executive Summary", "executive_summary"),
            ("Bid Strategy", "bid_strategy"),
            ("Risk Register", "risk_register"),
        ),
    },
    "SOURCE_REGISTER": {
        "title": "Evidence & Source Register",
        "formats": ("XLSX", "PDF"),
        "sections": (
            ("Source Register", "source_register"),
            ("Tender Facts", "tender_facts"),
            ("Technical Requirements", "technical_requirements"),
        ),
    },
}


PROVENANCE_LABELS = {
    "TENDER_EXTRACTED": "Extracted from Tender",
    "EXTERNALLY_VERIFIED": "Externally Verified",
    "AI_ASSESSMENT": "AI Assessment — Verify Before Reliance",
    "UNKNOWN": "Unknown / Unverified",
}


def _text(value):
    """Convert values safely to printable text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return "; ".join(_text(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(
            f"{_text(k)}: {_text(v)}" for k, v in value.items()
        )
    return str(value)


def _records(value):
    """Normalize a section into a list of dictionaries."""
    if value is None or value == "":
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            if isinstance(item, dict):
                result.append(item)
            else:
                result.append({"Information": item})
        return result
    return [{"Information": value}]


def _section_data(payload, key):
    analysis = payload.get("analysis") or {}
    value = analysis.get(key)

    # The executive summary is a scalar, not a list of records.
    if key == "executive_summary":
        return [{"Executive Summary": _text(value)}] if value else []

    return _records(value)


def _report_sections(artifact_type, payload):
    spec = ARTIFACTS[artifact_type]
    result = []

    for title, key in spec["sections"]:
        rows = _section_data(payload, key)
        if not rows:
            rows = [{
                "Status": "Not available in the saved structured analysis.",
                "Required Action": (
                    "Run or complete War Room AI analysis and verify "
                    "the original tender evidence."
                ),
            }]
        result.append((title, rows))

    # Include actual registered supplier records separately from AI strategy.
    if artifact_type == "SUPPLIER_INTELLIGENCE":
        registered = _records(payload.get("registered_suppliers"))
        if registered:
            result.append(("Company Registered Supplier Records", registered))

    # Include saved lot/item records where they add actual tender context.
    if artifact_type in (
        "TENDER_INTELLIGENCE",
        "TECHNICAL_COMPLIANCE",
        "COMMERCIAL_ANALYSIS",
    ):
        lots = _records(payload.get("lots"))
        if lots:
            result.append(("Saved Tender Lot and Item Records", lots))

    return result


def _metadata_rows(payload):
    tender = payload.get("tender") or {}
    if not isinstance(tender, dict):
        tender = {}

    fields = (
        ("Company", payload.get("company")),
        ("Tender Serial", tender.get("serial_number")),
        ("Tender Reference", tender.get("bid_reference_number")),
        ("Tender Title", tender.get("bid_title")),
        ("Purchaser", tender.get("purchaser_name")),
        ("Organization Type", tender.get("organization_type")),
        ("Published Date", tender.get("published_date")),
        ("Submission Date", tender.get("bid_submission_date")),
        ("Opening Date", tender.get("bid_opening_date")),
        ("Analysis Status", payload.get("analysis_status")),
        ("Analysis Provenance", "AI Assessment — verify against original tender"),
    )
    return [(label, _text(value)) for label, value in fields if value not in (None, "")]


def _provenance_text(payload):
    provenance = payload.get("provenance") or {}
    if isinstance(provenance, dict):
        return "\n".join(
            f"{_text(key)}: {PROVENANCE_LABELS.get(_text(value), _text(value))}"
            for key, value in provenance.items()
        )
    return _text(provenance)


def _validate(artifact_type, file_format, payload):
    if artifact_type not in ARTIFACTS:
        raise ValueError(f"Unsupported artifact type: {artifact_type}")

    file_format = str(file_format).upper()
    if file_format not in ARTIFACTS[artifact_type]["formats"]:
        raise ValueError(
            f"{file_format} is not an allowed format for {artifact_type}. "
            f"Allowed: {', '.join(ARTIFACTS[artifact_type]['formats'])}"
        )

    if not isinstance(payload, dict):
        raise TypeError("Document payload must be a dictionary.")

    return file_format


def _all_columns(rows):
    columns = []
    for row in rows:
        for key in row:
            name = _text(key)
            if name not in columns:
                columns.append(name)
    return columns or ["Information"]


def _render_pdf(title, payload, sections):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        LongTable, KeepTogether,
    )

    buffer = BytesIO()
    wide = any(
        len(_all_columns(rows)) > 5
        for _, rows in sections
    )
    page_size = landscape(A4) if wide else A4

    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="Infinity Investment Group",
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="InfinityTitle",
        parent=styles["Title"],
        textColor=colors.HexColor("#17365D"),
        alignment=TA_CENTER,
        fontSize=17,
        leading=21,
        spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="InfinityHeading",
        parent=styles["Heading2"],
        textColor=colors.HexColor("#17365D"),
        fontSize=11,
        leading=14,
        spaceBefore=12,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="InfinityCell",
        parent=styles["BodyText"],
        fontSize=7,
        leading=9,
        wordWrap="CJK",
    ))
    styles.add(ParagraphStyle(
        name="InfinitySmall",
        parent=styles["BodyText"],
        fontSize=8,
        leading=11,
    ))

    story = [
        Paragraph(escape(title), styles["InfinityTitle"]),
        Paragraph(
            "INFINITY INVESTMENT GROUP | CONFIDENTIAL PROCUREMENT DOCUMENT",
            styles["InfinitySmall"],
        ),
        Spacer(1, 8),
    ]

    metadata = _metadata_rows(payload)
    if metadata:
        story.append(Paragraph("Tender Identification", styles["InfinityHeading"]))
        table = Table(
            [
                [
                    Paragraph(f"<b>{escape(k)}</b>", styles["InfinityCell"]),
                    Paragraph(escape(v), styles["InfinityCell"]),
                ]
                for k, v in metadata
            ],
            colWidths=[42 * mm, doc.width - 42 * mm],
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAF0F7")),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#AAB5C3")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.extend([table, Spacer(1, 8)])

    for section_title, rows in sections:
        story.append(Paragraph(escape(section_title), styles["InfinityHeading"]))
        columns = _all_columns(rows)
        data = [[
            Paragraph(f"<b>{escape(col)}</b>", styles["InfinityCell"])
            for col in columns
        ]]

        for row in rows:
            data.append([
                Paragraph(
                    escape(_text(row.get(col, ""))).replace("\n", "<br/>"),
                    styles["InfinityCell"],
                )
                for col in columns
            ])

        available_width = doc.width
        col_width = available_width / max(len(columns), 1)
        table = LongTable(
            data,
            colWidths=[col_width] * len(columns),
            repeatRows=1,
            splitByRow=1,
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17365D")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F4F6F9")),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#AAB5C3")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.extend([table, Spacer(1, 6)])

    provenance = _provenance_text(payload)
    story.append(Paragraph("Evidence and Provenance Notice", styles["InfinityHeading"]))
    notice = (
        "Tender-extracted information must be checked against the original "
        "purchased tender document. AI assessments are recommendations, not "
        "independent verification. Company supplier records do not establish "
        "current authorization, eligibility, stock, pricing or delivery capacity. "
        "Missing evidence must remain identified as unknown or unverified."
    )
    story.append(Paragraph(escape(notice), styles["InfinitySmall"]))

    if provenance:
        story.append(Spacer(1, 5))
        story.append(Paragraph(
            escape(provenance).replace("\n", "<br/>"),
            styles["InfinitySmall"],
        ))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(
            14 * mm, 9 * mm,
            "INFINITY INVESTMENT GROUP | CONFIDENTIAL",
        )
        canvas.drawRightString(
            page_size[0] - 14 * mm, 9 * mm,
            f"Page {document.page}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _render_docx(title, payload, sections):
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.7)
    section.right_margin = Inches(0.7)

    heading = document.add_heading(title, 0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in heading.runs:
        run.font.color.rgb = RGBColor(23, 54, 93)

    p = document.add_paragraph(
        "INFINITY INVESTMENT GROUP | CONFIDENTIAL PROCUREMENT DOCUMENT"
    )
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    metadata = _metadata_rows(payload)
    if metadata:
        document.add_heading("Tender Identification", level=1)
        table = document.add_table(rows=0, cols=2)
        table.style = "Table Grid"
        for key, value in metadata:
            cells = table.add_row().cells
            cells[0].text = key
            cells[1].text = value

    for section_title, rows in sections:
        document.add_heading(section_title, level=1)
        columns = _all_columns(rows)
        table = document.add_table(rows=1, cols=len(columns))
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

        for index, col in enumerate(columns):
            cell = table.rows[0].cells[index]
            cell.text = col
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            for run in cell.paragraphs[0].runs:
                run.bold = True
                run.font.color.rgb = RGBColor(23, 54, 93)

        for row in rows:
            cells = table.add_row().cells
            for index, col in enumerate(columns):
                cells[index].text = _text(row.get(col, ""))
                cells[index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP

    document.add_heading("Evidence and Provenance Notice", level=1)
    document.add_paragraph(
        "Tender-extracted information must be checked against the original "
        "purchased tender document. AI assessments are recommendations, not "
        "independent verification. Registered supplier records do not establish "
        "current authorization, eligibility, stock, pricing or delivery capacity. "
        "Missing evidence must remain identified as unknown or unverified."
    )

    provenance = _provenance_text(payload)
    if provenance:
        document.add_paragraph(provenance)

    footer = section.footer.paragraphs[0]
    footer.text = "INFINITY INVESTMENT GROUP | CONFIDENTIAL"
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in footer.runs:
        run.font.size = Pt(8)

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _render_xlsx(title, payload, sections):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    info = workbook.active
    info.title = "Tender Overview"

    info.append(["Document", title])
    info.append(["Company", _text(payload.get("company"))])

    for key, value in _metadata_rows(payload):
        info.append([key, value])

    info.append([])
    info.append(["Evidence and Provenance Notice"])
    info.append([
        "Tender facts must be verified against the original tender. "
        "AI assessments are not independent verification. "
        "Supplier registration does not prove authorization, stock, price or capacity."
    ])

    header_fill = PatternFill("solid", fgColor="17365D")
    header_font = Font(color="FFFFFF", bold=True)
    thin = Side(style="thin", color="C5CDD8")

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.sheet_view.showGridLines = False

    for section_title, rows in sections:
        safe_title = "".join(
            ch for ch in section_title if ch.isalnum() or ch in " _-"
        )[:31] or "Data"
        base = safe_title
        suffix = 1
        while safe_title in workbook.sheetnames:
            suffix += 1
            safe_title = f"{base[:27]}_{suffix}"

        sheet = workbook.create_sheet(safe_title)
        columns = _all_columns(rows)
        sheet.append(columns)

        for row in rows:
            sheet.append([_text(row.get(col, "")) for col in columns])

        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False

        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(
                vertical="top", wrap_text=True
            )

        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(
                    vertical="top", wrap_text=True
                )
                cell.border = Border(bottom=thin)

        for column_cells in sheet.columns:
            letter = get_column_letter(column_cells[0].column)
            max_length = max(
                (len(_text(cell.value)) for cell in column_cells),
                default=10,
            )
            sheet.column_dimensions[letter].width = min(
                max(max_length + 2, 14), 48
            )

    for cell in info[1]:
        cell.fill = header_fill
        cell.font = header_font

    info.column_dimensions["A"].width = 28
    info.column_dimensions["B"].width = 85
    for row in info.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def render_artifact(artifact_type, file_format, payload):
    """
    Render one artifact and return (bytes, mime_type, filename).

    Expected payload:
    {
        "company": "Company name",
        "tender": {
            "serial_number": "...",
            "bid_reference_number": "...",
            "bid_title": "...",
            "purchaser_name": "...",
            ...
        },
        "analysis": { ... saved structured War Room JSON ... },
        "lots": [ ... actual saved lot/item records ... ],
        "registered_suppliers": [ ... actual company supplier records ... ],
        "analysis_status": "DONE",
        "provenance": { "Tender facts": "TENDER_EXTRACTED", ... }
    }
    """
    file_format = _validate(artifact_type, file_format, payload)
    spec = ARTIFACTS[artifact_type]
    title = spec["title"]
    sections = _report_sections(artifact_type, payload)

    if file_format == "PDF":
        content = _render_pdf(title, payload, sections)
        mime_type = "application/pdf"
        extension = "pdf"
    elif file_format == "DOCX":
        content = _render_docx(title, payload, sections)
        mime_type = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )
        extension = "docx"
    else:
        content = _render_xlsx(title, payload, sections)
        mime_type = (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )
        extension = "xlsx"

    tender = payload.get("tender") or {}
    serial = _text(tender.get("serial_number") or "Tender")
    safe_serial = "".join(
        ch if ch.isalnum() or ch in "-_" else "_"
        for ch in serial
    )
    filename = f"{artifact_type}_{safe_serial}.{extension}"

    return content, mime_type, filename
