from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.http import HttpResponse
from django.contrib import messages
import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from .models import Tender, Task
from .chain import create_task
from .services import log_action

@login_required
def bid_operator_workspace(request, pk):
    tender = get_object_or_404(Tender, pk=pk, company=getattr(request, 'company', None))
    emp = getattr(request.user, 'employee', None)
    
    if not emp or emp.role not in ('BIDOP', 'ADMIN'):
        return redirect('dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'save_stages':
            fields = [
                'bo_technical_proposal', 'bo_bid_forms', 'bo_compliance_matrix',
                'bo_certificates_datasheets', 'bo_mal', 'bo_landed_cost', 'bo_bid_price'
            ]
            for field in fields:
                if field in request.FILES:
                    setattr(tender, field, request.FILES[field])
            
            tender.bid_op_notes = request.POST.get('notes', '')
            tender.save()
            messages.success(request, 'Stage files uploaded successfully.')
            return redirect('bid_operator_workspace', pk=tender.id)

        elif action == 'complete':
            task = Task.objects.filter(payload__tender_id=tender.id, code='T10').first()
            if task:
                task.status = 'COMPLETED'
                task.save()
                
            create_task(
                tender.company, 'T13', 'CEO', 
                f'Set FX Rate & Final Price: {tender.bid_title}',
                'Bid Operator has assembled all documents. Please set the FX rate and final bid price.',
                {'tender_id': tender.id}, reverse('tender_detail', args=[tender.id])
            )
            log_action(emp, 'ASSEMBLE_COMPLETE', 'Completed T10 Assembly', 'Tender', tender.id, tender.company)
            return redirect('dashboard')

    return render(request, 'assembly_workspace.html', {'tender': tender})


@login_required
def generate_bo_report(request, pk):
    tender = get_object_or_404(Tender, pk=pk)
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Bid Operator Submission Report", styles['Heading1']))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Tender: %s - %s" % (tender.serial_number, tender.bid_title), styles['Normal']))
    story.append(Spacer(1, 20))

    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors

    data = [["Component", "Status", "File Name"]]
    fields = [
        ("Technical Proposal", tender.bo_technical_proposal),
        ("Bid Forms", tender.bo_bid_forms),
        ("Compliance Matrix", tender.bo_compliance_matrix),
        ("Certificates & Datasheets", tender.bo_certificates_datasheets),
        ("MAL (Optional)", tender.bo_mal),
        ("Landed Cost Calculation", tender.bo_landed_cost),
        ("Bid Price Proposal", tender.bo_bid_price),
    ]

    for name, file in fields:
        if file:
            status = "Uploaded"
            filename = file.name.split('/')[-1]
        else:
            status = "Missing" if name != "MAL (Optional)" else "Not Provided"
            filename = "-"
        data.append([name, status, filename])

    t = Table(data, colWidths=[150, 100, 250])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f204b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f4f4f9')),
    ]))
    story.append(t)
    
    story.append(Spacer(1, 20))
    story.append(Paragraph("Bid Operator Notes:", styles['Heading3']))
    story.append(Paragraph(tender.bid_op_notes or "No notes provided.", styles['Normal']))

    doc.build(story)
    buffer.seek(0)
    
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="BO_Report_%s.pdf"' % tender.serial_number
    return response
