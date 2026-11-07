from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Tender, Task
from .chain import create_task
from .services import notify, log_action
from django.urls import reverse

@login_required
def bid_operator_workspace(request, pk):
    tender = get_object_or_404(Tender, pk=pk, company=getattr(request, 'company', None))
    
    if request.method == 'POST':
        # Save the uploaded technical proposal
        if 'technical_proposal' in request.FILES:
            tender.final_technical_proposal = request.FILES['technical_proposal']
        
        tender.bid_op_notes = request.POST.get('notes', '')
        tender.save()
        
        log_action(request.user.employee, 'ASSEMBLE_BID', f'Uploaded final tech proposal for {tender.serial_number}', 'Tender', tender.id, tender.company)
        
        # Find the T10 task and mark it complete
        task = Task.objects.filter(payload__tender_id=tender.id, code='T10').first()
        if task:
            task.status = 'COMPLETED'
            task.save()
            
        # Create T13 (CEO Final Price Approval / FX Rate)
        ceo_task = create_task(
            tender.company, 'T13', 'CEO', 
            f'Set FX Rate & Final Price: {tender.bid_title}',
            'Technical proposal is assembled. Please set the FX rate and final bid price.',
            {'tender_id': tender.id}, reverse('tender_detail', args=[tender.id])
        )
        
        return redirect('dashboard')

    return render(request, 'assembly_workspace.html', {'tender': tender})
