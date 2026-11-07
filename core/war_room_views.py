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

@login_required
def war_room(request, pk):
    tender = get_object_or_404(Tender, pk=pk, company=getattr(request, 'company', None))
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'analyze' and tender.purchased_bid_document:
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
            threading.Thread(target=bg_ai).start()
            return redirect('war_room', pk=tender.id)
            
        elif action == 'manual_save':
            tender.war_room_summary = request.POST.get('summary', '')
            tender.war_room_checklist = request.POST.get('checklist', '')
            tender.war_room_brands = request.POST.get('brands', '')
            tender.war_room_suppliers = request.POST.get('suppliers', '')
            tender.war_room_compliance = request.POST.get('compliance', '')
            tender.save()
            return redirect('war_room', pk=tender.id)
            
        elif action == 'forward_task':
            section = request.POST.get('section')
            task = Task.objects.filter(payload__tender_id=tender.id, code='T09').first()
            if task and task.status != 'COMPLETED':
                task.status = 'COMPLETED'
                task.save()
            
            create_task(
                tender.company, 'T10', 'BIDOP', 
                f'Review {section.title()} for {tender.serial_number}',
                f'Bid Manager forwarded the {section}. Please review and assemble.',
                {'tender_id': tender.id, 'section': section}, reverse('tender_detail', args=[tender.id])
            )
            return redirect('dashboard')

    return render(request, 'war_room.html', {'tender': tender})

@login_required
def download_war_room_pdf(request, pk):
    tender = get_object_or_404(Tender, pk=pk)
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
