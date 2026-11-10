import io
import threading
import csv
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpResponse
from django.urls import reverse
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from .models import Tender, Task
from .gemini import analyze_bid_document, parse_war_room_output
from .chain import create_task
from .services import log_action

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

        if action == 'analyze' and tender.purchased_bid_document:
            if tender.ai_status == 'PROCESSING':
                messages.warning(request, 'AI analysis is already processing.')
                return redirect('war_room', pk=tender.id)

            tender.ai_status = 'PROCESSING'
            tender.ai_error = ''
            tender.save()

            def bg_ai():
                try:
                    raw_result = analyze_bid_document(tender.purchased_bid_document)
                    parsed = parse_war_room_output(raw_result)
                    tender.war_room_summary = parsed.get('summary', '')
                    tender.war_room_checklist = parsed.get('checklist', '')
                    tender.war_room_brands = parsed.get('brands', '')
                    tender.war_room_suppliers = parsed.get('suppliers', '')
                    tender.war_room_compliance = parsed.get('compliance', '')
                    tender.ai_status = 'DONE'
                    tender.save()
                except Exception as e:
                    tender.ai_error = str(e)
                    tender.ai_status = 'FAILED'
                    tender.save()
                finally:
                    connection.close()

            threading.Thread(target=bg_ai, daemon=True).start()
            return redirect('war_room', pk=tender.id)

        elif action == 'manual_save':
            tender.war_room_summary = request.POST.get('summary', '')
            tender.war_room_checklist = request.POST.get('checklist', '')
            tender.war_room_brands = request.POST.get('brands', '')
            tender.war_room_suppliers = request.POST.get('suppliers', '')
            tender.war_room_compliance = request.POST.get('compliance', '')
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

    return render(request, 'war_room.html', {'tender': tender})

@login_required
def download_war_room_pdf(request, pk):
    emp, company, membership, tender, task = _war_room_context(request, pk)
    buffer = io.BytesIO()
    
    # We use a custom canvas to add headers/footers to every page
    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            canvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []
        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()
        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.draw_page_number(num_pages)
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)
        def draw_page_number(self, page_count):
            self.setFont("Helvetica", 9)
            self.setFillColor(colors.gray)
            self.drawString(40, 30, "Infinity Investment Institute - Confidential Bid Strategy")
            self.drawRightString(555, 30, f"Page {self._pageNumber} of {page_count}")

    # Determine orientation based on if we have a matrix
    pagesize = landscape(A4) if tender.war_room_compliance else A4
    doc = SimpleDocTemplate(buffer, pagesize=pagesize, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=24, textColor=colors.HexColor('#0f204b'), alignment=1, spaceAfter=20)
    h1_style = ParagraphStyle('H1', parent=styles['Heading2'], fontSize=16, textColor=colors.white, backColor=colors.HexColor('#0f204b'), borderPadding=8, spaceBefore=20, spaceAfter=10)
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=11, leading=14, spaceAfter=8)
    
    story = []

    # Cover Page
    story.append(Paragraph(f"Bid Strategy Report", title_style))
    story.append(Paragraph(f"{tender.serial_number} - {tender.bid_title}", ParagraphStyle('Sub', parent=styles['Heading2'], alignment=1, textColor=colors.HexColor('#d4af37'))))
    story.append(Spacer(1, 200))
    story.append(Paragraph(f"Prepared For: {tender.purchaser_name}", ParagraphStyle('Center', alignment=1)))
    story.append(Paragraph(f"Generated On: {tender.updated_at.strftime('%d %b %Y')}", ParagraphStyle('Center', alignment=1)))
    story.append(PageBreak())

    # Sections
    sections = [
        ("1. Executive Summary & Requirements", tender.war_room_summary),
        ("2. Submission Checklist", tender.war_room_checklist),
        ("3. Brand Sourcing Matrix", tender.war_room_brands),
        ("4. Supplier Intelligence", tender.war_room_suppliers),
    ]

    for title, content in sections:
        story.append(Paragraph(title, h1_style))
        story.append(Spacer(1, 10))
        for line in content.split('\n'):
            if line.strip():
                # Simple markdown parsing
                line = line.replace('**', '')
                story.append(Paragraph(line, body_style))
        story.append(PageBreak())

    # Compliance Matrix Table
    story.append(Paragraph("5. Compliance Matrix", h1_style))
    story.append(Spacer(1, 10))
    
    if tender.war_room_compliance:
        try:
            # Parse the CSV
            csv_reader = csv.reader(io.StringIO(tender.war_room_compliance))
            table_data = []
            for row in csv_reader:
                if row and len(row) > 1: # Ignore empty rows
                    table_data.append([Paragraph(cell, body_style) for cell in row])
            
            if table_data:
                # Style the table
                t = Table(table_data, colWidths=[150, 180, 150, 100, 150])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f204b')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f4f4f9')),
                    ('GRID', (0, 0), (-1, -1), 1, colors.gray),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ]))
                # Make first row header bold
                for i in range(len(table_data[0])):
                    table_data[0][i] = Paragraph(f"<b>{table_data[0][i].text}</b>", body_style)
                
                story.append(t)
        except Exception as e:
            story.append(Paragraph(f"Error parsing matrix: {e}", body_style))
    else:
        story.append(Paragraph("No compliance matrix data available.", body_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Bid_Strategy_{tender.serial_number}.pdf"'
    return response
