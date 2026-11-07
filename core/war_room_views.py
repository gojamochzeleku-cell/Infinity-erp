import io
import threading
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpResponse
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from .models import Tender
from .gemini import analyze_bid_document, parse_war_room_output
from .chain import route_t09_tasks

@login_required
def war_room(request, pk):
    tender = get_object_or_404(Tender, pk=pk, company=getattr(request, 'company', None))
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'analyze' and tender.purchased_bid_document:
            tender.ai_status = 'PROCESSING'
            tender.save()
            
            def bg_ai():
                try:
                    raw_result = analyze_bid_document(tender.purchased_bid_document)
                    parsed = parse_war_room_output(raw_result)
                    tender.war_room_summary = parsed['summary']
                    tender.war_room_checklist = parsed['checklist']
                    tender.war_room_brands = parsed['brands']
                    tender.war_room_suppliers = parsed['suppliers']
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
            tender.save()
            return redirect('war_room', pk=tender.id)
            
        elif action == 'forward':
            # Forward to Bid Operator (T10)
            task = create_task(
                tender.company, 'T10', 'BIDOP', 
                f'Assemble Bid Documents: {tender.bid_title}',
                'Bid Manager has finalized the strategy. Assemble the technical proposal.',
                {'tender_id': tender.id}, reverse('tender_detail', args=[tender.id])
            )
            task.status = 'COMPLETED' # Complete the T09 task
            task.save()
            return redirect('dashboard')

    return render(request, 'war_room.html', {'tender': tender})

@login_required
def download_war_room_pdf(request, pk):
    tender = get_object_or_404(Tender, pk=pk)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    title = Paragraph(f"Bid Strategy Report: {tender.serial_number}", styles['Heading1'])
    story.append(title)
    story.append(Spacer(1, 12))

    for section_name, content in [
        ("Executive Summary", tender.war_room_summary),
        ("Submission Checklist", tender.war_room_checklist),
        ("Brand Sourcing Matrix", tender.war_room_brands),
        ("Supplier Intelligence", tender.war_room_suppliers),
    ]:
        story.append(Paragraph(section_name, styles['Heading2']))
        story.append(Spacer(1, 6))
        # Simple formatting: split by newlines
        for line in content.split('\n'):
            if line.strip():
                story.append(Paragraph(line, styles['Normal']))
                story.append(Spacer(1, 4))
        story.append(Spacer(1, 15))

    doc.build(story)
    buffer.seek(0)
    
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Bid_Strategy_{tender.serial_number}.pdf"'
    return response
