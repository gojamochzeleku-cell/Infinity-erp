import io
import threading
import csv
import re
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import close_old_connections
from django.http import HttpResponse
from django.urls import reverse
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, PageBreak
from reportlab.lib.utils import simpleSplit
from xml.sax.saxutils import escape
from reportlab.lib import colors
from .models import Tender, Task, TenderLot, Supplier, WarRoomArtifact
from .gemini import analyze_bid_document, parse_war_room_output
from .chain import create_task
from .services import log_action
from .war_room_artifact_service import generate_war_room_artifacts

def _war_room_context(request, pk):
    """Validate the active company membership and assigned Bid Manager task."""
    from django.core.exceptions import PermissionDenied

    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or emp is None
        or not emp.is_active
        or company is None
        or not company.is_active
        or membership is None
        or not membership.is_active
        or membership.employee_id != emp.id
        or membership.company_id != company.id
    ):
        raise PermissionDenied("Active company membership required.")

    roles = set(membership.roles.values_list('role', flat=True))
    if 'BIDM' not in roles:
        raise PermissionDenied("Bid Manager company role required.")

    tender = get_object_or_404(Tender, pk=pk, company=company)

    task = get_object_or_404(
        Task,
        company=company,
        code='T09',
        assigned_to=emp,
        payload__tender_id=tender.id,
    )

    return emp, company, membership, tender, task


@login_required
def war_room(request, pk):
    from django.db import transaction
    from django.contrib import messages

    emp, company, membership, tender, task = _war_room_context(request, pk)

    if request.method == 'POST':
        if task.status not in ('PENDING', 'IN_PROGRESS'):
            messages.error(request, 'This War Room task is no longer active.')
            return redirect('dashboard')

        action = request.POST.get('action')

        if action == 'generate_artifacts':
            try:
                generated = generate_war_room_artifacts(
                    tender, company, emp, task
                )
                messages.success(
                    request,
                    f'{len(generated)} War Room documents generated as drafts.',
                )
            except Exception as exc:
                messages.error(
                    request,
                    f'Could not generate War Room documents: {str(exc)[:500]}',
                )
            return redirect('war_room', pk=tender.id)

        elif action == 'analyze' and tender.purchased_bid_document:
            if tender.ai_status == 'PROCESSING':
                messages.warning(request, 'AI analysis is already processing.')
                return redirect('war_room', pk=tender.id)

            tender.ai_status = 'PROCESSING'
            tender.ai_error = ''
            tender.save()

            tender_id = tender.id
            company_id = company.id

            def bg_ai():
                close_old_connections()
                try:
                    ai_tender = Tender.objects.get(
                        pk=tender_id,
                        company_id=company_id,
                    )
                    supplier_rows = Supplier.objects.filter(
                        company_id=company_id
                    ).values(
                        'name', 'contact_person', 'phone', 'email',
                        'rating', 'supplier_rating', 'notes'
                    )
                    supplier_context = "\n".join(
                        " | ".join(
                            f"{field}: {row.get(field) or 'Not recorded'}"
                            for field in (
                                'name', 'contact_person', 'phone', 'email',
                                'rating', 'supplier_rating', 'notes'
                            )
                        )
                        for row in supplier_rows
                    )
                    raw_result = analyze_bid_document(
                        ai_tender.purchased_bid_document,
                        supplier_context=supplier_context
                    )
                    parsed = parse_war_room_output(raw_result)

                    # Do not report success when the AI returned only an error
                    # or an unusable/empty response.
                    if not any(parsed.get(key) for key in (
                        'summary', 'checklist', 'compliance', 'brands',
                        'suppliers', 'model_comparison', 'source_register',
                        'supplier_intelligence'
                    )):
                        raise ValueError("AI returned no usable procurement analysis.")

                    ai_tender.war_room_summary = parsed.get('summary', '')
                    ai_tender.war_room_checklist = parsed.get('checklist', '')
                    ai_tender.war_room_brands = parsed.get('brands', '')
                    ai_tender.war_room_suppliers = parsed.get('suppliers', '')
                    ai_tender.war_room_compliance = parsed.get('compliance', '')
                    ai_tender.war_room_data = parsed.get('structured_data') or {}
                    ai_tender.ai_status = 'DONE'
                    ai_tender.ai_error = ''
                    ai_tender.save()
                except Exception as exc:
                    Tender.objects.filter(
                        pk=tender_id,
                        company_id=company_id,
                    ).update(
                        ai_status='FAILED',
                        ai_error=str(exc)[:4000],
                    )
                finally:
                    close_old_connections()

            threading.Thread(target=bg_ai, daemon=True).start()
            return redirect('war_room', pk=tender.id)

        elif action in ('approve_lot', 'reject_lot'):
            lot_id = request.POST.get('lot_id', '').strip()
            if not lot_id.isdigit():
                messages.error(request, 'Invalid lot selection.')
                return redirect('war_room', pk=tender.id)

            with transaction.atomic():
                locked_lot = get_object_or_404(
                    TenderLot.objects.select_for_update(),
                    pk=int(lot_id),
                    tender=tender,
                )

                if locked_lot.status != 'PENDING':
                    messages.warning(
                        request,
                        'This lot is no longer awaiting Bid Manager review.',
                    )
                    return redirect('war_room', pk=tender.id)

                if action == 'reject_lot':
                    locked_lot.status = 'REJECTED_BY_BIDM'
                    locked_lot.save(update_fields=['status'])
                    log_action(
                        emp, 'LOT_REJECTED_BY_BIDM',
                        f'Rejected {locked_lot.lot_name}',
                        'TenderLot', locked_lot.id, company,
                    )
                    messages.success(
                        request,
                        f'{locked_lot.lot_name} rejected. It remains with BIDM.',
                    )
                else:
                    existing = Task.objects.filter(
                        company=company,
                        code='T10_LOT',
                        payload__tender_id=tender.id,
                        payload__lot_id=locked_lot.id,
                        status__in=('PENDING', 'IN_PROGRESS'),
                    ).exists()

                    if existing:
                        messages.warning(
                            request,
                            'An active BIDOP task already exists for this lot.',
                        )
                    else:
                        created = create_task(
                            company,
                            'T10_LOT',
                            'BIDOP',
                            f'Assemble Bid: {locked_lot.lot_name} ({tender.serial_number})',
                            f'BIDM approved {locked_lot.lot_name}. Prepare its technical and financial documents.',
                            {
                                'tender_id': tender.id,
                                'lot_id': locked_lot.id,
                            },
                            reverse('tender_detail', args=[tender.id]),
                        )

                        if created is None:
                            messages.error(
                                request,
                                'No designated BIDOP is available. '
                                'The lot remains pending and has not been approved.',
                            )
                        else:
                            locked_lot.status = 'APPROVED_FOR_BIDOP'
                            locked_lot.save(update_fields=['status'])
                            log_action(
                                emp, 'LOT_APPROVED_FOR_BIDOP',
                                f'Approved {locked_lot.lot_name} for BIDOP',
                                'TenderLot', locked_lot.id, company,
                            )
                            messages.success(
                                request,
                                f'{locked_lot.lot_name} approved and assigned to BIDOP.',
                            )

            return redirect('war_room', pk=tender.id)

        elif action == 'manual_save':
            tender.war_room_summary = request.POST.get('summary', '')
            tender.war_room_checklist = request.POST.get('checklist', '')
            tender.war_room_brands = request.POST.get('brands', '')
            tender.war_room_suppliers = request.POST.get('suppliers', '')
            tender.war_room_compliance = request.POST.get('compliance', '')
            for upload_key, model_field in (
                ('manual_summary', 'bm_manual_summary'),
                ('manual_checklist', 'bm_manual_checklist'),
                ('manual_brands', 'bm_manual_brands'),
                ('manual_suppliers', 'bm_manual_suppliers'),
            ):
                uploaded = request.FILES.get(upload_key)
                if uploaded:
                    setattr(tender, model_field, uploaded)
            tender.save()
            messages.success(request, 'War Room changes saved.')
            return redirect('war_room', pk=tender.id)

        elif action == 'forward_task':
            section = request.POST.get('section', '').strip()
            allowed_sections = {'summary', 'checklist', 'brands', 'suppliers', 'all'}

            if section not in allowed_sections:
                messages.error(request, 'Invalid War Room section.')
                return redirect('war_room', pk=tender.id)

            with transaction.atomic():
                locked_task = Task.objects.select_for_update().get(
                    pk=task.pk,
                    company=company,
                    assigned_to=emp,
                )

                if locked_task.status not in ('PENDING', 'IN_PROGRESS'):
                    messages.error(request, 'This War Room task is no longer active.')
                    return redirect('dashboard')

                duplicate = Task.objects.filter(
                    company=company,
                    code='T10',
                    payload__tender_id=tender.id,
                    payload__section=section,
                ).exists()

                if duplicate:
                    messages.warning(
                        request,
                        f'The {section} section has already been forwarded.'
                    )
                    return redirect('war_room', pk=tender.id)

                created = create_task(
                    company,
                    'T10',
                    'BIDOP',
                    f'Review {section.title()} for {tender.serial_number}',
                    f'Bid Manager forwarded the {section}. Please review and assemble.',
                    {'tender_id': tender.id, 'section': section},
                    reverse('tender_detail', args=[tender.id]),
                )

                if created is None:
                    messages.error(
                        request,
                        'No active designated Bid Operator is assigned to this company. '
                        'The War Room task remains open.'
                    )
                    return redirect('war_room', pk=tender.id)

                if section == 'all':
                    locked_task.status = 'COMPLETED'
                    locked_task.save(update_fields=['status', 'updated_at'])
                else:
                    locked_task.status = 'IN_PROGRESS'
                    locked_task.save(update_fields=['status', 'updated_at'])

                log_action(
                    emp,
                    'WAR_ROOM_FORWARD',
                    f'Forwarded {section} for tender {tender.serial_number}',
                    'Tender',
                    tender.id,
                    company,
                )

            messages.success(request, f'{section.title()} forwarded to the Bid Operator.')
            return redirect('dashboard')

    artifacts = WarRoomArtifact.objects.filter(
        company=company, tender=tender
    ).order_by('artifact_type', '-version_number', '-created_at')
    return render(request, 'war_room.html', {
        'tender': tender,
        'artifacts': artifacts,
    })

@login_required
def download_war_room_artifact(request, pk, artifact_id):
    emp, company, membership, tender, task = _war_room_context(request, pk)
    artifact = get_object_or_404(
        WarRoomArtifact,
        pk=artifact_id,
        company=company,
        tender=tender,
    )
    if not artifact.file:
        from django.http import Http404
        raise Http404("Artifact file is unavailable.")

    with artifact.file.open("rb") as stored_file:
        response = HttpResponse(
            stored_file.read(),
            content_type=artifact.mime_type or "application/octet-stream",
        )
    response["Content-Disposition"] = (
        f'attachment; filename="{artifact.original_filename}"'
    )
    return response


@login_required
def download_war_room_pdf(request, pk):
    """
    Generate a company- and BIDM-authorized strategy report.
    Escape all model/AI text and use a wrapping, repeating compliance table.
    """
    emp, company, membership, tender, task = _war_room_context(request, pk)

    buffer = io.BytesIO()
    pagesize = landscape(A4) if tender.war_room_compliance else A4
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        rightMargin=34,
        leftMargin=34,
        topMargin=42,
        bottomMargin=42,
        title=f"Bid Strategy - {tender.serial_number}",
        author="Infinity ERP"
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InfinityReportTitle", parent=styles["Title"],
        fontSize=22, leading=27, textColor=colors.HexColor("#0f204b"),
        alignment=1, spaceAfter=14
    )
    subtitle_style = ParagraphStyle(
        "InfinityReportSubtitle", parent=styles["Heading2"],
        fontSize=12, leading=16, textColor=colors.HexColor("#a77b19"),
        alignment=1, spaceAfter=12
    )
    heading_style = ParagraphStyle(
        "InfinitySectionHeading", parent=styles["Heading2"],
        fontSize=13, leading=17, textColor=colors.white,
        backColor=colors.HexColor("#0f204b"),
        borderPadding=7, spaceBefore=14, spaceAfter=9
    )
    body_style = ParagraphStyle(
        "InfinityReportBody", parent=styles["BodyText"],
        fontSize=8.5, leading=12, spaceAfter=5,
        wordWrap="CJK"
    )
    small_style = ParagraphStyle(
        "InfinityReportSmall", parent=body_style,
        fontSize=7, leading=9
    )
    cell_style = ParagraphStyle(
        "InfinityTableCell", parent=body_style,
        fontSize=6.5, leading=8, spaceAfter=0
    )
    header_cell_style = ParagraphStyle(
        "InfinityTableHeader", parent=cell_style,
        textColor=colors.white, fontName="Helvetica-Bold"
    )

    story = [
        Paragraph("BID STRATEGY & PROCUREMENT INTELLIGENCE REPORT", title_style),
        Paragraph(
            escape(str(tender.serial_number or "")) + " — "
            + escape(str(tender.bid_title or "")),
            subtitle_style
        ),
        Paragraph(
            "<b>Purchaser:</b> " + escape(str(tender.purchaser_name or "Not recorded")),
            body_style
        ),
        Paragraph(
            "<b>Company:</b> " + escape(str(company)),
            body_style
        ),
        Paragraph(
            "<b>Report generated:</b> "
            + escape(tender.updated_at.strftime("%d %b %Y %H:%M")),
            body_style
        ),
        Spacer(1, 12),
        Paragraph(
            "<b>Important:</b> AI-generated decision support. Verify every "
            "requirement against the original tender document and obtain "
            "documentary evidence before submission.",
            body_style
        )
    ]

    def add_text_section(title, content):
        story.append(Paragraph(escape(title), heading_style))
        if not content or not str(content).strip():
            story.append(Paragraph("No information recorded for this section.", body_style))
            return
        for line in str(content).splitlines():
            clean = line.strip()
            if clean:
                story.append(
                    Paragraph(escape(clean).replace("\n", "<br/>"), body_style)
                )

    add_text_section("1. Executive Summary, Tender Facts and Strategy",
                     tender.war_room_summary)
    add_text_section("2. Submission Checklist", tender.war_room_checklist)
    add_text_section("3. Brand Sourcing Intelligence", tender.war_room_brands)
    add_text_section("4. Supplier Intelligence", tender.war_room_suppliers)

    story.append(Paragraph("5. Technical Compliance Matrix", heading_style))

    if tender.war_room_compliance:
        try:
            rows = list(csv.reader(io.StringIO(tender.war_room_compliance)))
            rows = [row for row in rows if row and any(cell.strip() for cell in row)]

            if rows:
                column_count = max(len(row) for row in rows)
                rows = [
                    row + [""] * (column_count - len(row))
                    for row in rows
                ]

                # Widths adapt to the actual number of columns and page size.
                weights = [0.8, 0.8, 0.9, 1.5, 2.0, 0.7, 1.3,
                           1.2, 1.0, 1.4, 0.7]
                if column_count != len(weights):
                    weights = [1.0] * column_count
                weights = weights[:column_count]
                total_weight = sum(weights)
                col_widths = [
                    doc.width * weight / total_weight
                    for weight in weights
                ]

                table_data = []
                for row_index, row in enumerate(rows):
                    style = header_cell_style if row_index == 0 else cell_style
                    table_data.append([
                        Paragraph(
                            escape(str(value)).replace("\n", "<br/>"),
                            style
                        )
                        for value in row
                    ])

                table = LongTable(
                    table_data,
                    colWidths=col_widths,
                    repeatRows=1,
                    splitByRow=1,
                    hAlign="LEFT"
                )
                table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f204b")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f4f5f8")),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#aab1bd")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(table)
            else:
                story.append(Paragraph("No compliance rows were recorded.", body_style))
        except Exception:
            story.append(Paragraph(
                "The saved compliance matrix could not be parsed as CSV. "
                "Review the editable matrix in the War Room.",
                body_style
            ))
    else:
        story.append(Paragraph("No compliance matrix data available.", body_style))

    manual_files = [
        ("Manual summary", tender.bm_manual_summary),
        ("Manual checklist", tender.bm_manual_checklist),
        ("Manual brands", tender.bm_manual_brands),
        ("Manual suppliers", tender.bm_manual_suppliers),
    ]
    uploaded_names = []
    for label, file_field in manual_files:
        if file_field:
            uploaded_names.append(f"{label}: {file_field.name}")

    if uploaded_names:
        story.append(Paragraph("6. Supporting Manual Documents", heading_style))
        for name in uploaded_names:
            story.append(Paragraph(escape(name), small_style))

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            page_count = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.setFont("Helvetica", 7)
                self.setFillColor(colors.grey)
                self.drawString(
                    34, 22,
                    "INFINITY INVESTMENT GROUP | CONFIDENTIAL BID STRATEGY"
                )
                self.drawRightString(
                    doc.pagesize[0] - 34, 22,
                    f"Page {self._pageNumber} of {page_count}"
                )
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)

    safe_serial = re.sub(
        r"[^A-Za-z0-9_-]+", "_", str(tender.serial_number or tender.pk)
    )
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="Bid_Strategy_{safe_serial}.pdf"'
    )
    response["Content-Length"] = str(len(buffer.getvalue()))
    return response
