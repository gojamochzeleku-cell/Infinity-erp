from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from decimal import Decimal
from .models import Tender, Task
from .chain import create_task
from .services import log_action

@login_required
def ceo_pricing(request, pk):
    tender = get_object_or_404(Tender, pk=pk, company=getattr(request, 'company', None))
    emp = getattr(request.user, 'employee', None)
    
    if not emp or emp.role not in ('CEO', 'GM', 'ADMIN'):
        return redirect('dashboard')

    if request.method == 'POST':
        # Save pricing data
        tender.fx_rate = Decimal(request.POST.get('fx_rate', '0'))
        tender.profit_margin_percent = Decimal(request.POST.get('profit_margin', '0'))
        tender.final_bid_price = Decimal(request.POST.get('final_price', '0'))
        
        # Save signature if provided
        sig = request.POST.get('signature_data')
        if sig:
            tender.ceo_signature = sig
            
        tender.save()
        
        log_action(emp, 'CEO_LOCK_PRICE', f'Locked final price at {tender.final_bid_price} ETB', 'Tender', tender.id, tender.company)
        
        # Complete T13 Task
        task = Task.objects.filter(payload__tender_id=tender.id, code='T13').first()
        if task:
            task.status = 'COMPLETED'
            task.save()
            
        # Create T14 (Finance - Bank Letter)
        create_task(
            tender.company, 'T14', 'FIN',
            f'Prepare Bank Letter & CPO: {tender.bid_title}',
            'The CEO has locked the final price. Please prepare the Bid Security (CPO) and Bank Letter.',
            {'tender_id': tender.id}, reverse('tender_detail', args=[tender.id])
        )
        
        return redirect('dashboard')

    return render(request, 'ceo_pricing.html', {'tender': tender})
