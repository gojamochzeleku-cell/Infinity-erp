"""
Pure document rendering for the BIDM War Room.

This module:
- Does not query or modify Django models.
- Does not write files or database records.
- Renders PDF, DOCX and XLSX from a prepared payload.
- Keeps tender facts, AI assessments and unverified information distinct.
"""

from io import BytesIO
from xml.sax.saxutils import escape, quoteattr
from urllib.parse import urlparse


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
        "sections": (
            ("Tender Technical Requirements", "technical_requirements"),
            ("Candidate Model Technical Verification", "model_comparison"),
        ),
    },
    "BRAND_COMPARISON": {
        "title": "Brand & Model Comparison Report",
        "formats": ("PDF", "DOCX", "XLSX"),
        "sections": (
            ("Requirement-by-Requirement Model Comparison", "model_comparison"),
            ("Tender Brand, Equivalent and Authorization Conditions", "brand_sourcing"),
        ),
    },
    "SUPPLIER_INTELLIGENCE": {
        "title": "Supplier Intelligence & Evaluation Report",
        "formats": ("PDF", "DOCX", "XLSX"),
        "sections": (
            ("Live Manufacturer, Distributor and Supplier Research",
             "supplier_intelligence"),
            ("Registered Supplier Strategy and Evaluation",
             "supplier_strategy"),
        ),
    },
    "COMMERCIAL_ANALYSIS": {
        "title": "Commercial & Landed Cost Analysis",
        "formats": ("XLSX", "PDF"),
        "sections": (
            ("Commercial Analysis", "commercial_analysis"),
            ("Lot Commercial Data", "lot_analysis"),
            ("Commercial Missing-Input Reconciliation",
             "commercial_gap_register"),
            ("Lot-by-Lot Commercial Bid Readiness Decision",
             "commercial_readiness"),
        ),
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


def _safe_url(value):
    """Accept only complete HTTP(S) URLs for clickable document links."""
    value = str(value or "").strip()
    try:
        parsed = urlparse(value)
        if parsed.scheme.lower() in ("http", "https") and parsed.netloc:
            return value
    except (TypeError, ValueError):
        pass
    return None


def _is_url_column(name):
    name = str(name or "").lower()
    return any(term in name for term in ("url", "website", "link"))


def _add_docx_hyperlink(paragraph, url, display_text):
    """Insert an external hyperlink into a DOCX paragraph."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE

    relationship_id = paragraph.part.relate_to(
        url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True
    )

    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)

    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")

    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    properties.append(color)

    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.append(underline)

    run.append(properties)

    text_element = OxmlElement("w:t")
    text_element.text = display_text
    run.append(text_element)

    hyperlink.append(run)
    paragraph._p.append(hyperlink)


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

    rows = _records(value)

    # Preserve the actual Google Search grounding evidence captured
    # during War Room analysis. Search queries are research trails,
    # not proof of technical compliance.
    if key == "source_register":
        research = analysis.get("_web_research") or {}

        for source in research.get("sources", []):
            if isinstance(source, dict):
                rows.append({
                    "Source Type": source.get(
                        "source_type", "GOOGLE_SEARCH_GROUNDING"
                    ),
                    "Source Title": source.get("title", ""),
                    "Source URL": source.get("url", ""),
                    "Evidence Classification": (
                        "Search grounding reference; verify the "
                        "original source before relying on it."
                    ),
                })

        for query in research.get("search_queries", []):
            if query:
                rows.append({
                    "Source Type": "GOOGLE_SEARCH_QUERY",
                    "Search Query": str(query),
                    "Evidence Classification": (
                        "Research query only; not technical evidence."
                    ),
                })

        # Include item-level evidence URLs reported by model and supplier
        # research, preserving their relationship to the tender candidate.
        evidence_sources = (
            (
                "model_comparison",
                (
                    ("official_product_url", "OFFICIAL_PRODUCT_PAGE"),
                    ("manufacturer_datasheet_url", "MANUFACTURER_DATASHEET"),
                ),
            ),
            (
                "supplier_intelligence",
                (
                    ("evidence_url", "SUPPLIER_EVIDENCE"),
                    ("official_website", "SUPPLIER_OFFICIAL_WEBSITE"),
                    ("official_contact_or_sales_url", "SUPPLIER_CONTACT_PAGE"),
                ),
            ),
        )

        for data_key, url_fields in evidence_sources:
            for record in _records(analysis.get(data_key)):
                for url_field, source_type in url_fields:
                    url = record.get(url_field)
                    if not url:
                        continue

                    rows.append({
                        "Source Type": source_type,
                        "Source Title": record.get("evidence_title", ""),
                        "Source URL": url,
                        "Related Item or Lot": record.get(
                            "item_or_lot",
                            record.get("relevant_item_or_lot", ""),
                        ),
                        "Candidate Model": record.get(
                            "candidate_brand", ""
                        ) + " " + record.get("candidate_model", ""),
                        "Supplier": record.get("supplier_name", ""),
                        "Evidence Type": record.get("evidence_type", ""),
                        "Verification Status": record.get(
                            "verification_status",
                            record.get("compliance_status", "NOT_VERIFIED"),
                        ),
                        "Evidence Classification": (
                            "AI-reported candidate source URL; open and "
                            "verify the original source before relying on it."
                        ),
                    })

    return rows


def _report_sections(artifact_type, payload):
    spec = ARTIFACTS[artifact_type]
    result = []

    for title, key in spec["sections"]:
        rows = _section_data(payload, key)
        if not rows:
            missing_sections = {
                "model_comparison": (
                    "No candidate-by-requirement model assessment was "
                    "available in the saved analysis.",
                    "Complete technical extraction and live manufacturer "
                    "research. Verify every candidate against each "
                    "mandatory tender specification.",
                ),
                "supplier_intelligence": (
                    "No supplier intelligence findings were available "
                    "in the saved analysis.",
                    "Research relevant manufacturers, distributors and "
                    "exporters. Verify supplier identity, authorization "
                    "and evidence from original sources.",
                ),
                "source_register": (
                    "No source records were available in the saved "
                    "analysis. No source-backed conclusion can be "
                    "established from this section.",
                    "Complete source-grounded research and record source "
                    "titles, URLs, evidence types and verification status.",
                ),
            }
            finding, action = missing_sections.get(
                key,
                (
                    "This section was not populated in the saved "
                    "structured analysis. This is not a negative finding.",
                    "Complete the relevant War Room analysis and verify "
                    "the result against the original tender document "
                    "and supporting evidence.",
                ),
            )
            rows = [{
                "Status": "NOT_ANALYZED",
                "Finding": finding,
                "Required Action": action,
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


def _column_width_weights(columns):
    weights = []

    for column in columns:
        name = str(column).lower()

        if any(term in name for term in (
            "requirement", "specification", "description",
            "details", "recommendation", "deviation", "gap",
            "capability", "qualification", "evidence",
            "products", "action", "justification", "notes",
        )):
            weight = 3.2
        elif any(term in name for term in (
            "url", "website", "link",
        )):
            weight = 2.2
        elif any(term in name for term in (
            "status", "country", "quantity", "date",
            "page", "type", "lot", "serial", "reference",
        )):
            weight = 1.0
        else:
            weight = 1.6

        weights.append(weight)

    return weights


def _status_fill(value):
    status = str(value or "").strip().upper()

    if status in ("COMPLIANT", "COMPLY"):
        return "D9EAD3"
    if status in ("PARTIAL", "PARTIALLY COMPLIANT"):
        return "FFF2CC"
    if status in ("NON_COMPLIANT", "NON-COMPLIANT"):
        return "F4CCCC"
    if status in ("NOT_VERIFIED", "NOT VERIFIED", "NOT_ANALYZED",
                  "NOT ANALYZED"):
        return "E7E6E6"

    return None


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
                    (
                        '<link href=' + quoteattr(_safe_url(_text(row.get(col, "")))) +
                        '>' + escape(_safe_url(_text(row.get(col, "")))) +
                        '</link>'
                        if _is_url_column(col) and _safe_url(_text(row.get(col, "")))
                        else escape(_text(row.get(col, ""))).replace("\n", "<br/>")
                    ),
                    styles["InfinityCell"],
                )
                for col in columns
            ])

        available_width = doc.width
        weights = _column_width_weights(columns)
        total_weight = sum(weights) or 1
        column_widths = [
            available_width * weight / total_weight
            for weight in weights
        ]
        table = LongTable(
            data,
            colWidths=column_widths,
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
        status_columns = [
            index for index, column in enumerate(columns)
            if "status" in str(column).lower()
            or "compliance" in str(column).lower()
        ]
        for row_index, row in enumerate(rows, start=1):
            for column_index in status_columns:
                fill = _status_fill(row.get(columns[column_index], ""))
                if fill:
                    table.setStyle(TableStyle([
                        (
                            "BACKGROUND",
                            (column_index, row_index),
                            (column_index, row_index),
                            colors.HexColor("#" + fill),
                        )
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
    from docx.enum.section import WD_ORIENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.7)
    section.right_margin = Inches(0.7)

    if any(len(_all_columns(rows)) > 5 for _, rows in sections):
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = (
            section.page_height, section.page_width
        )

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
        table.autofit = False

        usable_width = (
            section.page_width - section.left_margin - section.right_margin
        ) / 914400
        weights = _column_width_weights(columns)
        total_weight = sum(weights) or 1
        column_widths = [
            usable_width * weight / total_weight
            for weight in weights
        ]

        for index, width in enumerate(column_widths):
            table.columns[index].width = Inches(width)
            for table_row in table.rows:
                table_row.cells[index].width = Inches(width)

        for index, col in enumerate(columns):
            cell = table.rows[0].cells[index]
            cell.text = col
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            for run in cell.paragraphs[0].runs:
                run.bold = True
                run.font.color.rgb = RGBColor(23, 54, 93)
                run.font.size = Pt(8)

        for row in rows:
            cells = table.add_row().cells
            for index, col in enumerate(columns):
                value = _text(row.get(col, ""))
                url = _safe_url(value) if _is_url_column(col) else None
                if url:
                    _add_docx_hyperlink(cells[index].paragraphs[0], url, url)
                else:
                    cells[index].text = value

                cells[index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP

                if "status" in col.lower() or "compliance" in col.lower():
                    normalized = value.strip().upper().replace(" ", "_")
                    fill = {
                        "COMPLIANT": "D9EAD3",
                        "COMPLY": "D9EAD3",
                        "PARTIAL": "FFF2CC",
                        "PARTIALLY_COMPLIANT": "FFF2CC",
                        "NON_COMPLIANT": "F4CCCC",
                        "NON-COMPLIANT": "F4CCCC",
                        "NOT_VERIFIED": "E7E6E6",
                        "NOT-VERIFIED": "E7E6E6",
                        "NOT_ANALYZED": "E7E6E6",
                        "NOT-ANALYZED": "E7E6E6",
                    }.get(normalized)

                    if fill:
                        shading = OxmlElement("w:shd")
                        shading.set(qn("w:fill"), fill)
                        cells[index]._tc.get_or_add_tcPr().append(shading)

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


def _render_xlsx(title, payload, sections, artifact_type=None):
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
            excel_row = sheet.max_row
            for index, col in enumerate(columns, start=1):
                value = _text(row.get(col, ""))
                url = _safe_url(value) if _is_url_column(col) else None
                if url:
                    cell = sheet.cell(row=excel_row, column=index)
                    cell.hyperlink = url
                    cell.style = "Hyperlink"

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
            column_index = column_cells[0].column - 1
            letter = get_column_letter(column_cells[0].column)
            column_name = str(columns[column_index]).lower()

            max_length = max(
                (len(_text(cell.value)) for cell in column_cells),
                default=10,
            )

            if any(term in column_name for term in (
                "requirement", "specification", "description",
                "details", "recommendation", "deviation",
                "gap", "evidence", "capability", "qualification",
                "action", "notes",
            )):
                width = 48
            elif any(term in column_name for term in (
                "url", "website", "link",
            )):
                width = 42
            elif "status" in column_name or "compliance" in column_name:
                width = 20
            elif any(term in column_name for term in (
                "quantity", "date", "page", "country", "type",
            )):
                width = 16
            else:
                width = 24

            sheet.column_dimensions[letter].width = min(
                max(width, min(max_length + 2, 18)), 52
            )

        # Highlight compliance and verification statuses.
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                column_name = str(
                    columns[cell.column - 1]
                ).lower()

                if "status" in column_name or "compliance" in column_name:
                    fill = _status_fill(cell.value)
                    if fill:
                        cell.fill = PatternFill(
                            "solid", fgColor=fill
                        )

    for cell in info[1]:
        cell.fill = header_fill
        cell.font = header_font

    info.column_dimensions["A"].width = 28
    info.column_dimensions["B"].width = 85
    for row in info.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    if artifact_type == "COMMERCIAL_ANALYSIS":
        cost_rows = _records(
            (payload.get("analysis") or {}).get("commercial_analysis")
        )
        if cost_rows:
            from decimal import Decimal, InvalidOperation

            def _excel_numeric(value):
                raw = str(value or "").strip()
                if not raw:
                    return raw
                try:
                    number = Decimal(raw)
                    if not number.is_finite():
                        return raw
                    return float(number)
                except (InvalidOperation, ValueError):
                    return raw

            cost_sheet = workbook.create_sheet("Landed Cost Detail")
            cost_columns = [
                "Item / Lot", "Cost Component", "Amount", "Currency",
                "Quantity", "Unit", "Unit Cost", "Exchange Rate",
                "Exchange Rate Basis", "Calculation Formula",
                "Calculation Result", "Cost Status",
                "Assumptions / Exclusions", "Source Page",
                "Amount Basis", "Verified Excel Unit Cost",
                "Excel Calculation Status",
                "Cost Inclusion", "Delivery Basis",
                "Included Extended Cost",
            ]
            cost_sheet.append(cost_columns)

            for record in cost_rows:
                amount = _excel_numeric(record.get("amount", ""))
                quantity = _excel_numeric(record.get("quantity", ""))
                basis = str(
                    record.get("amount_basis") or "UNKNOWN"
                ).strip().upper()
                unit = str(record.get("unit") or "").strip()

                cost_sheet.append([
                    _text(record.get("item_or_lot", "")),
                    _text(record.get("cost_component", "")),
                    amount,
                    _text(record.get("currency", "")),
                    quantity,
                    unit,
                    _text(record.get("unit_cost", "")),
                    _text(record.get("exchange_rate", "")),
                    _text(record.get("exchange_rate_source_or_basis", "")),
                    _text(record.get("calculation_formula", "")),
                    _text(record.get("calculation_result", "")),
                    _text(record.get("cost_status", "")),
                    _text(record.get("assumption_or_exclusion", "")),
                    _text(record.get("source_page", "")),
                    basis,
                    "",
                    "",
                    _text(record.get("cost_inclusion") or "UNKNOWN").strip().upper(),
                    _text(record.get("delivery_basis", "")),
                    "",
                ])

                excel_row = cost_sheet.max_row
                amount_ok = isinstance(amount, (int, float))
                quantity_ok = isinstance(quantity, (int, float))

                if basis == "UNIT" and amount_ok and unit:
                    cost_sheet.cell(
                        row=excel_row, column=16
                    ).value = f"=C{excel_row}"
                    status = "Calculated from documented unit amount"

                elif (
                    basis == "TOTAL"
                    and amount_ok
                    and quantity_ok
                    and quantity > 0
                    and unit
                ):
                    cost_sheet.cell(
                        row=excel_row, column=16
                    ).value = f"=C{excel_row}/E{excel_row}"
                    status = "Calculated from total / verified quantity"

                else:
                    status = (
                        "NOT CALCULATED: verify amount basis, numeric "
                        "amount, positive quantity and unit"
                    )

                cost_sheet.cell(
                    row=excel_row, column=17
                ).value = status

                inclusion = str(
                    record.get("cost_inclusion") or "UNKNOWN"
                ).strip().upper()
                if inclusion == "INCLUDE" and amount_ok:
                    if basis == "TOTAL":
                        cost_sheet.cell(
                            row=excel_row, column=20
                        ).value = f"=C{excel_row}"
                    elif (
                        basis == "UNIT"
                        and quantity_ok
                        and quantity > 0
                        and unit
                    ):
                        cost_sheet.cell(
                            row=excel_row, column=20
                        ).value = f"=C{excel_row}*E{excel_row}"

                cost_sheet.cell(
                    row=excel_row, column=20
                ).number_format = "#,##0.00"

                cost_sheet.cell(
                    row=excel_row, column=16
                ).number_format = '#,##0.00'

            # Cost reconciliation ledger: preserve every source record,
            # distinguish evidence classes, and flag possible duplicates
            # without automatically excluding or deleting any record.
            reconciliation_sheet = workbook.create_sheet(
                "Cost Reconciliation"
            )

            reconciliation_sheet.append([
                "Item / Lot",
                "Currency",
                "Delivery Basis",
                "Cost Component",
                "Original Amount",
                "Amount Basis",
                "Cost Status",
                "Cost Inclusion",
                "Linked Extended Cost",
                "Verified Included Amount",
                "User Input Included Amount",
                "Pending / Unverified Amount",
                "Reconciliation Treatment",
                "Duplicate Review",
                "Duplicate Match Count",
                "Source Page",
                "Assumptions / Exclusions",
                "Required Review Action",
            ])

            def _duplicate_text(value):
                return re.sub(
                    r"\\s+", " ",
                    _text(value).strip().lower()
                )

            duplicate_keys = []
            for record in cost_rows:
                duplicate_keys.append((
                    _duplicate_text(record.get("item_or_lot")),
                    _duplicate_text(record.get("cost_component")),
                    _duplicate_text(record.get("currency")),
                    _duplicate_text(record.get("delivery_basis")),
                    _duplicate_text(record.get("amount")),
                    _duplicate_text(record.get("amount_basis")),
                ))

            duplicate_counts = Counter(duplicate_keys)

            for index, record in enumerate(cost_rows, start=2):
                status = _text(
                    record.get("cost_status") or "UNKNOWN"
                ).strip().upper()
                inclusion = _text(
                    record.get("cost_inclusion") or "UNKNOWN"
                ).strip().upper()

                key = duplicate_keys[index - 2]
                match_count = duplicate_counts[key]
                possible_duplicate = match_count > 1

                if inclusion == "EXCLUDE":
                    treatment = "EXCLUDED — REVIEW BASIS"
                    action = (
                        "Confirm the exclusion reason and ensure the "
                        "cost is not required elsewhere."
                    )
                elif inclusion == "UNKNOWN":
                    treatment = "UNRESOLVED — INCLUSION UNKNOWN"
                    action = (
                        "Determine whether this cost belongs in the "
                        "bid price and record supporting evidence."
                    )
                elif status == "VERIFIED":
                    treatment = "INCLUDE — VERIFIED SOURCE STATUS"
                    action = (
                        "Check original quotation, invoice or other "
                        "supporting evidence and delivery scope."
                    )
                elif status == "USER_INPUT":
                    treatment = "INCLUDE — USER INPUT, NOT INDEPENDENTLY VERIFIED"
                    action = (
                        "Obtain documentary support before treating "
                        "this input as verified."
                    )
                elif status == "QUOTATION_REQUIRED":
                    treatment = "OPEN — QUOTATION REQUIRED"
                    action = (
                        "Obtain a supplier quotation; do not treat "
                        "a placeholder as a confirmed cost."
                    )
                elif status == "INPUT_REQUIRED":
                    treatment = "OPEN — INPUT REQUIRED"
                    action = (
                        "Obtain the missing amount, quantity, unit "
                        "or calculation basis."
                    )
                elif status == "NOT_APPLICABLE":
                    treatment = "NOT APPLICABLE — VERIFY REASON"
                    action = (
                        "Retain the documented reason supporting "
                        "non-applicability."
                    )
                else:
                    treatment = "OPEN — STATUS REQUIRES REVIEW"
                    action = (
                        "Verify the cost status and supporting evidence."
                    )

                if possible_duplicate:
                    action += (
                        " Compare this record with the other matching "
                        "records; confirm whether it is a duplicate or "
                        "a legitimate separate charge."
                    )

                reconciliation_sheet.append([
                    _text(record.get("item_or_lot", "")),
                    _text(record.get("currency", "")),
                    _text(record.get("delivery_basis", "")),
                    _text(record.get("cost_component", "")),
                    _excel_numeric(record.get("amount", "")),
                    _text(record.get("amount_basis") or "UNKNOWN").upper(),
                    status,
                    inclusion,
                    "",
                    "",
                    "",
                    "",
                    treatment,
                    (
                        "POSSIBLE DUPLICATE — MANUAL REVIEW"
                        if possible_duplicate else "NO EXACT MATCH FOUND"
                    ),
                    match_count,
                    _text(record.get("source_page", "")),
                    _text(record.get("assumption_or_exclusion", "")),
                    action,
                ])

                row_number = reconciliation_sheet.max_row
                source_row = index

                reconciliation_sheet.cell(
                    row=row_number, column=9
                ).value = (
                    f'=IF(AND(\'Landed Cost Detail\'!R{source_row}="INCLUDE",'
                    f'\'Landed Cost Detail\'!T{source_row}<>""),'
                    f'\'Landed Cost Detail\'!T{source_row},"")'
                )

                reconciliation_sheet.cell(
                    row=row_number, column=10
                ).value = (
                    f'=IF(AND(H{row_number}="INCLUDE",'
                    f'G{row_number}="VERIFIED",I{row_number}<>""),'
                    f'I{row_number},"")'
                )

                reconciliation_sheet.cell(
                    row=row_number, column=11
                ).value = (
                    f'=IF(AND(H{row_number}="INCLUDE",'
                    f'G{row_number}="USER_INPUT",I{row_number}<>""),'
                    f'I{row_number},"")'
                )

                reconciliation_sheet.cell(
                    row=row_number, column=12
                ).value = (
                    f'=IF(AND(H{row_number}="INCLUDE",'
                    f'OR(G{row_number}="QUOTATION_REQUIRED",'
                    f'G{row_number}="INPUT_REQUIRED",'
                    f'G{row_number}="UNKNOWN"),I{row_number}<>""),'
                    f'I{row_number},"")'
                )

                for col in range(9, 13):
                    reconciliation_sheet.cell(
                        row=row_number, column=col
                    ).number_format = "#,##0.00"

            # Reconciliation totals remain separated by lot, currency
            # and delivery basis. User inputs and unresolved costs are
            # displayed separately from verified included costs.
            reconciliation_groups = sorted({
                (
                    _text(record.get("item_or_lot", "")),
                    _text(record.get("currency", "")),
                    _text(record.get("delivery_basis", "")),
                )
                for record in cost_rows
            })

            summary_start = reconciliation_sheet.max_row + 3
            reconciliation_sheet.cell(
                row=summary_start, column=1
            ).value = "LOT-BY-LOT COST RECONCILIATION"

            summary_headers = [
                "Item / Lot",
                "Currency",
                "Delivery Basis",
                "Verified Included Total",
                "User Input Total",
                "Pending / Unverified Total",
                "Excluded Record Count",
                "Unknown Inclusion Count",
                "Possible Duplicate Record Count",
                "Reconciliation Note",
            ]

            for col, value in enumerate(summary_headers, start=1):
                reconciliation_sheet.cell(
                    row=summary_start + 1, column=col
                ).value = value

            for lot, currency, delivery in reconciliation_groups:
                reconciliation_sheet.append([
                    lot, currency, delivery, "", "", "", "", "", "",
                    "Review open items and duplicate flags before "
                    "using this reconciliation for bid pricing.",
                ])

                summary_row = reconciliation_sheet.max_row
                first_data_row = 2
                last_data_row = summary_start - 1

                criteria = (
                    f"$A${first_data_row}:$A${last_data_row},A{summary_row},"
                    f"$B${first_data_row}:$B${last_data_row},B{summary_row},"
                    f"$C${first_data_row}:$C${last_data_row},C{summary_row}"
                )

                for col, sum_col in ((4, "J"), (5, "K"), (6, "L")):
                    reconciliation_sheet.cell(
                        row=summary_row, column=col
                    ).value = (
                        f'=IF(COUNTIFS({criteria})=0,"",'
                        f'SUMIFS(${sum_col}${first_data_row}:'
                        f'${sum_col}${last_data_row},{criteria}))'
                    )
                    reconciliation_sheet.cell(
                        row=summary_row, column=col
                    ).number_format = "#,##0.00"

                reconciliation_sheet.cell(
                    row=summary_row, column=7
                ).value = (
                    f'=COUNTIFS({criteria},$H${first_data_row}:'
                    f'$H${last_data_row},"EXCLUDE")'
                )

                reconciliation_sheet.cell(
                    row=summary_row, column=8
                ).value = (
                    f'=COUNTIFS({criteria},$H${first_data_row}:'
                    f'$H${last_data_row},"UNKNOWN")'
                )

                reconciliation_sheet.cell(
                    row=summary_row, column=9
                ).value = (
                    f'=COUNTIFS({criteria},$N${first_data_row}:'
                    f'$N${last_data_row},"POSSIBLE DUPLICATE — MANUAL REVIEW")'
                )

            reconciliation_sheet.freeze_panes = "A2"
            reconciliation_sheet.sheet_view.showGridLines = False
            reconciliation_sheet.auto_filter.ref = (
                f"A1:R{summary_start - 1}"
            )

            for row_number in (1, summary_start, summary_start + 1):
                for cell in reconciliation_sheet[row_number]:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(
                        vertical="top", wrap_text=True
                    )

            reconciliation_widths = {
                "A": 22, "B": 12, "C": 22, "D": 28,
                "E": 18, "F": 16, "G": 22, "H": 18,
                "I": 20, "J": 22, "K": 22, "L": 24,
                "M": 38, "N": 34, "O": 18, "P": 14,
                "Q": 42, "R": 58,
            }

            for letter, width in reconciliation_widths.items():
                reconciliation_sheet.column_dimensions[letter].width = width

            for row in reconciliation_sheet.iter_rows():
                for cell in row:
                    cell.alignment = Alignment(
                        vertical="top", wrap_text=True
                    )
                    if cell.row not in (
                        1, summary_start, summary_start + 1
                    ):
                        cell.border = Border(bottom=thin)

            reconciliation_sheet.cell(
                row=reconciliation_sheet.max_row + 2,
                column=1
            ).value = "RECONCILIATION RULES"

            reconciliation_sheet.cell(
                row=reconciliation_sheet.max_row,
                column=2
            ).value = (
                "Verified and user-input amounts are separated. "
                "Quotation-required and input-required records remain "
                "open. EXCLUDE and UNKNOWN records are not included in "
                "verified totals. Possible duplicates are flags for "
                "manual review only; no record is automatically removed."
            )

            reconciliation_sheet.merge_cells(
                start_row=reconciliation_sheet.max_row,
                start_column=2,
                end_row=reconciliation_sheet.max_row,
                end_column=18,
            )

            # Separate summaries: never combine lots, currencies,
            # or delivery bases.
            summary_groups = sorted({
                (
                    _text(record.get("item_or_lot", "")),
                    _text(record.get("currency", "")),
                    _text(record.get("delivery_basis", "")),
                )
                for record in cost_rows
            })

            summary_sheet = workbook.create_sheet("Lot Commercial Summary")
            summary_sheet.append([
                "Item / Lot",
                "Currency",
                "Delivery Basis",
                "Included Extended Cost",
                "Calculation Status",
            ])

            for lot, currency, delivery_basis in summary_groups:
                summary_sheet.append([
                    lot, currency, delivery_basis, "", ""
                ])
                summary_row = summary_sheet.max_row

                summary_sheet.cell(
                    row=summary_row, column=4
                ).value = (
                    f'=IF(COUNTIFS('
                    f"'Landed Cost Detail'!$A:$A,A{summary_row},"
                    f"'Landed Cost Detail'!$D:$D,B{summary_row},"
                    f"'Landed Cost Detail'!$S:$S,C{summary_row},"
                    f"'Landed Cost Detail'!$R:$R,\"INCLUDE\","
                    f"'Landed Cost Detail'!$T:$T,\"<>\")=0,\"\","
                    f"SUMIFS('Landed Cost Detail'!$T:$T,"
                    f"'Landed Cost Detail'!$A:$A,A{summary_row},"
                    f"'Landed Cost Detail'!$D:$D,B{summary_row},"
                    f"'Landed Cost Detail'!$S:$S,C{summary_row},"
                    f"'Landed Cost Detail'!$R:$R,\"INCLUDE\"))'
                )

                summary_sheet.cell(
                    row=summary_row, column=5
                ).value = (
                    f'=IF(D{summary_row}="","'
                    f'NO CALCULABLE INCLUDED COSTS",'
                    f'"CALCULATED FROM INCLUDED ROWS")'
                )
                summary_sheet.cell(
                    row=summary_row, column=4
                ).number_format = "#,##0.00"

            summary_sheet.append([])
            summary_sheet.append([
                "Important",
                "Only explicit INCLUDE rows with supported numeric "
                "amounts and amount bases contribute. EXCLUDE and UNKNOWN "
                "rows remain visible in Landed Cost Detail but are not "
                "included in totals. Lots, currencies and delivery bases "
                "are kept separate."
            ])
            summary_sheet.merge_cells(
                start_row=summary_sheet.max_row,
                start_column=2,
                end_row=summary_sheet.max_row,
                end_column=5,
            )

            summary_sheet.freeze_panes = "A2"
            summary_sheet.sheet_view.showGridLines = False
            summary_sheet.auto_filter.ref = (
                f"A1:E{summary_sheet.max_row - 2}"
            )

            for cell in summary_sheet[1]:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(
                    vertical="top", wrap_text=True
                )

            for row in summary_sheet.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = Alignment(
                        vertical="top", wrap_text=True
                    )
                    cell.border = Border(bottom=thin)

            for column, width in {
                "A": 28, "B": 16, "C": 22, "D": 26, "E": 38
            }.items():
                summary_sheet.column_dimensions[column].width = width

            cost_sheet.freeze_panes = "A2"
            cost_sheet.auto_filter.ref = cost_sheet.dimensions
            cost_sheet.sheet_view.showGridLines = False

            for cell in cost_sheet[1]:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(
                    vertical="top", wrap_text=True
                )

            for row in cost_sheet.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = Alignment(
                        vertical="top", wrap_text=True
                    )
                    cell.border = Border(bottom=thin)

            cost_widths = {
                "A": 22, "B": 28, "C": 18, "D": 12,
                "E": 14, "F": 12, "G": 18, "H": 16,
                "I": 32, "J": 38, "K": 22, "L": 24,
                "M": 40, "N": 14, "O": 16, "P": 24,
                "Q": 58,
            }
            for letter, width in cost_widths.items():
                cost_sheet.column_dimensions[letter].width = width

    if artifact_type == "COMMERCIAL_ANALYSIS":
        profitability_rows = _records(
            (payload.get("analysis") or {}).get(
                "commercial_profitability"
            )
        )

        if profitability_rows:
            profit_sheet = workbook.create_sheet(
                "Profitability & Margin"
            )

            profit_sheet.append([
                "Item / Lot",
                "Selling Price",
                "Price Basis",
                "Quantity",
                "Currency",
                "Delivery Basis",
                "Cost Coverage",
                "Total Selling Revenue",
                "Verified Included Cost",
                "Gross Profit",
                "Markup %",
                "Gross Margin %",
                "Calculation Status",
                "Selling Price Evidence",
                "Verified Cost Evidence",
                "Included Cost Scope",
                "Limitations",
                "Source Pages",
            ])

            for record in profitability_rows:
                selling_price = _excel_numeric(
                    record.get("selling_price_amount", "")
                )
                quantity = _excel_numeric(record.get("quantity", ""))
                basis = str(
                    record.get("selling_price_basis") or "UNKNOWN"
                ).strip().upper()
                coverage = str(
                    record.get("cost_coverage_status") or "UNKNOWN"
                ).strip().upper()
                unit = str(record.get("unit") or "").strip()

                profit_sheet.append([
                    _text(record.get("item_or_lot", "")),
                    selling_price,
                    basis,
                    quantity,
                    _text(record.get("currency", "")),
                    _text(record.get("delivery_basis", "")),
                    coverage,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "NOT CALCULATED",
                    _text(record.get("selling_price_evidence", "")),
                    _text(record.get("verified_cost_evidence", "")),
                    _text(record.get("included_cost_scope", "")),
                    _text(record.get("assumptions_or_limitations", "")),
                    _text(record.get("source_pages", "")),
                ])

                r = profit_sheet.max_row
                price_ok = isinstance(selling_price, (int, float))
                quantity_ok = (
                    isinstance(quantity, (int, float))
                    and quantity > 0
                )

                if coverage == "COMPLETE" and price_ok and basis == "TOTAL":
                    profit_sheet.cell(
                        row=r, column=8
                    ).value = f"=B{r}"

                elif (
                    coverage == "COMPLETE"
                    and price_ok
                    and basis == "UNIT"
                    and quantity_ok
                    and unit
                ):
                    profit_sheet.cell(
                        row=r, column=8
                    ).value = f"=B{r}*D{r}"

                # Require at least one included cost row, and require
                # every matching INCLUDE row to have an extended cost.
                sheet_ref = "'Landed Cost Detail'!"
                criteria = (
                    f"{sheet_ref}$A:$A,A{r},"
                    f"{sheet_ref}$D:$D,E{r},"
                    f"{sheet_ref}$S:$S,F{r}"
                )
                included_count = (
                    f'COUNTIFS({criteria},{sheet_ref}$R:$R,"INCLUDE")'
                )
                calculated_count = (
                    f'COUNTIFS({criteria},{sheet_ref}$R:$R,"INCLUDE",'
                    f'{sheet_ref}$T:$T,"<>")'
                )

                cost_formula = (
                    f'=IF(AND(G{r}="COMPLETE",'
                    f'{included_count}>0,'
                    f'{calculated_count}={included_count}),'
                    f'SUMIFS({sheet_ref}$T:$T,{criteria},'
                    f'{sheet_ref}$R:$R,"INCLUDE"),"")'
                )
                profit_sheet.cell(
                    row=r, column=9
                ).value = cost_formula

                profit_sheet.cell(
                    row=r, column=10
                ).value = (
                    f'=IF(OR(H{r}="",I{r}=""),"",H{r}-I{r})'
                )
                profit_sheet.cell(
                    row=r, column=11
                ).value = (
                    f'=IF(OR(J{r}="",I{r}="",I{r}<=0),"",J{r}/I{r})'
                )
                profit_sheet.cell(
                    row=r, column=12
                ).value = (
                    f'=IF(OR(J{r}="",H{r}="",H{r}<=0),"",J{r}/H{r})'
                )
                profit_sheet.cell(
                    row=r, column=13
                ).value = (
                    f'=IF(OR(G{r}<>"COMPLETE",H{r}="",I{r}="",'
                    f'I{r}<=0,N{r}="",O{r}=""),'
                    f'"NOT CALCULATED","CALCULATED — VERIFY EVIDENCE")'
                )

                for col in (8, 9, 10):
                    profit_sheet.cell(
                        row=r, column=col
                    ).number_format = "#,##0.00"

                for col in (11, 12):
                    profit_sheet.cell(
                        row=r, column=col
                    ).number_format = "0.00%"

            profit_sheet.freeze_panes = "A2"
            profit_sheet.auto_filter.ref = profit_sheet.dimensions
            profit_sheet.sheet_view.showGridLines = False

            for cell in profit_sheet[1]:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(
                    vertical="top", wrap_text=True
                )

            profit_widths = {
                "A": 22, "B": 18, "C": 16, "D": 14,
                "E": 12, "F": 24, "G": 18, "H": 22,
                "I": 24, "J": 18, "K": 16, "L": 18,
                "M": 32, "N": 42, "O": 42, "P": 38,
                "Q": 48, "R": 18,
            }
            for letter, width in profit_widths.items():
                profit_sheet.column_dimensions[letter].width = width

            for row in profit_sheet.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = Alignment(
                        vertical="top", wrap_text=True
                    )
                    cell.border = Border(bottom=thin)

    if artifact_type == "COMMERCIAL_ANALYSIS":
        scenario_sheet = workbook.create_sheet(
            "Sensitivity Scenarios"
        )

        scenario_sheet.append([
            "Item / Lot",
            "Currency",
            "Delivery Basis",
            "Baseline Included Cost",
            "Baseline Selling Revenue",
            "Supplier Price Change %",
            "FX Change %",
            "Freight / Insurance Change %",
            "Other Cost Change %",
            "Scenario Cost",
            "Scenario Gross Profit",
            "Scenario Markup %",
            "Scenario Gross Margin %",
            "Scenario Status",
            "Scenario Assumptions",
        ])

        profit_records = _records(
            (payload.get("analysis") or {}).get(
                "commercial_profitability"
            )
        )

        for record in profit_records:
            lot = _text(record.get("item_or_lot", ""))
            currency = _text(record.get("currency", ""))
            delivery = _text(record.get("delivery_basis", ""))
            coverage = str(
                record.get("cost_coverage_status") or "UNKNOWN"
            ).strip().upper()

            selling_price = _excel_numeric(
                record.get("selling_price_amount", "")
            )
            quantity = _excel_numeric(record.get("quantity", ""))
            price_basis = str(
                record.get("selling_price_basis") or "UNKNOWN"
            ).strip().upper()

            revenue_formula = ""
            if (
                coverage == "COMPLETE"
                and isinstance(selling_price, (int, float))
                and price_basis == "TOTAL"
            ):
                revenue_formula = (
                    f'=IF(AND(D{scenario_sheet.max_row + 1}<>""),'
                    f'E{scenario_sheet.max_row + 1},"")'
                )
            elif (
                coverage == "COMPLETE"
                and isinstance(selling_price, (int, float))
                and price_basis == "UNIT"
                and isinstance(quantity, (int, float))
                and quantity > 0
            ):
                revenue_formula = (
                    f'=IF(AND(D{scenario_sheet.max_row + 1}<>""),'
                    f'E{scenario_sheet.max_row + 1},"")'
                )

            scenario_sheet.append([
                lot,
                currency,
                delivery,
                "",
                selling_price if price_basis == "TOTAL" else (
                    selling_price * quantity
                    if (
                        isinstance(selling_price, (int, float))
                        and isinstance(quantity, (int, float))
                        and quantity > 0
                    ) else ""
                ),
                0,
                0,
                0,
                0,
                "",
                "",
                "",
                "",
                "NOT CALCULATED",
                "Hypothetical percentage changes only. "
                "Zero means no scenario change, not a verified cost.",
            ])

            r = scenario_sheet.max_row

            # Baseline cost is linked to the matching profitability
            # worksheet row. That worksheet itself requires complete
            # coverage and matching included-cost evidence.
            profit_row = profit_records.index(record) + 2
            scenario_sheet.cell(
                row=r, column=4
            ).value = (
                f'=IF(\'Profitability & Margin\'!M{profit_row}'
                f'="CALCULATED — VERIFY EVIDENCE",'
                f'\'Profitability & Margin\'!I{profit_row},"")'
            )

            # Supplier-price, freight and other changes are scenario
            # assumptions. FX change applies to the FX-sensitive share
            # of baseline cost, not to the entire cost automatically.
            scenario_sheet.cell(
                row=r, column=10
            ).value = (
                f'=IF(OR(D{r}="",'
                f'\'Profitability & Margin\'!M{profit_row}'
                f'<>"CALCULATED — VERIFY EVIDENCE"),"",'
                f'D{r}*(1+F{r}/100)*(1+G{r}/100)'
                f'*(1+H{r}/100)*(1+I{r}/100))'
            )

            scenario_sheet.cell(
                row=r, column=11
            ).value = (
                f'=IF(OR(J{r}="",E{r}=""),"",E{r}-J{r})'
            )
            scenario_sheet.cell(
                row=r, column=12
            ).value = (
                f'=IF(OR(K{r}="",J{r}="",J{r}<=0),"",K{r}/J{r}*100)'
            )
            scenario_sheet.cell(
                row=r, column=13
            ).value = (
                f'=IF(OR(K{r}="",E{r}="",E{r}<=0),"",K{r}/E{r}*100)'
            )
            scenario_sheet.cell(
                row=r, column=14
            ).value = (
                f'=IF(OR(J{r}="",E{r}=""),'
                f'"NOT CALCULATED","HYPOTHETICAL SCENARIO")'
            )

            for col in (4, 5, 10, 11):
                scenario_sheet.cell(
                    row=r, column=col
                ).number_format = "#,##0.00"

            for col in (6, 7, 8, 9, 12, 13):
                scenario_sheet.cell(
                    row=r, column=col
                ).number_format = "0.00"

        scenario_sheet.freeze_panes = "A2"
        scenario_sheet.auto_filter.ref = scenario_sheet.dimensions
        scenario_sheet.sheet_view.showGridLines = False

        for cell in scenario_sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(
                vertical="top", wrap_text=True
            )

        scenario_widths = {
            "A": 22, "B": 12, "C": 24, "D": 22,
            "E": 22, "F": 20, "G": 16, "H": 28,
            "I": 22, "J": 20, "K": 22, "L": 18,
            "M": 22, "N": 28, "O": 58,
        }
        for letter, width in scenario_widths.items():
            scenario_sheet.column_dimensions[letter].width = width

        for row in scenario_sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(
                    vertical="top", wrap_text=True
                )
                cell.border = Border(bottom=thin)

        scenario_sheet.append([])
        scenario_sheet.append([
            "IMPORTANT",
            "All percentage changes are editable hypothetical assumptions. "
            "The FX change is a simplified whole-cost multiplier, not a "
            "currency-specific exposure model. Replace it with a documented "
            "FX-sensitive cost allocation before relying on the scenario."
        ])
        scenario_sheet.merge_cells(
            start_row=scenario_sheet.max_row,
            start_column=2,
            end_row=scenario_sheet.max_row,
            end_column=15,
        )

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
        content = _render_xlsx(title, payload, sections, artifact_type=artifact_type)
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
