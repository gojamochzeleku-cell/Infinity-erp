import io
import threading
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpResponse
from django.urls import reverse
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
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
            
            if 'manual_file' in request.FILES:
                tender.bm_manual_upload = request.FILES['manual_file']
                
            tender.save()
            return redirect('war_room', pk=tender.id)
            
        elif action == 'forward':
            # Find the T09 task and mark it complete
            task = Task.objects.filter(payload__tender_id=tender.id, code='T09').first()
            if task:
                task.status = 'COMPLETED'
                task.save()
            
            # Create T10 for the Bid Operator
            create_task(
                tender.company, 'T10', 'BIDOP', 
                f'Assemble Bid Documents: {tender.bid_title}',
                'Bid Manager has finalized the strategy. Assemble the technical proposal using the provided documents.',
                {'tender_id': tender.id}, reverse('tender_detail', args=[tender.id])
            )
            
            log_action(request.user.employee, 'FORWARD_TO_BIDOP', 'Forwarded War Room to Bid Operator', 'Tender', tender.id, tender.company)
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
