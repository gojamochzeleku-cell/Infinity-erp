from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from decimal import Decimal
from .models import Tender, Task, TenderLot
from .chain import create_task
from .services import log_action

@login_required
def ceo_lot_pricing(request, pk, lot_id):
    tender = get_object_or_404(Tender, pk=pk, company=getattr(request, 'company', None))
    lot = get_object_or_404(TenderLot, pk=lot_id, tender=tender)
    emp = getattr(request.user, 'employee', None)
    
    if not emp or emp.role not in ('CEO', 'GM', 'ADMIN'):
        return redirect('dashboard')

    if request.method == 'POST':
        # Save pricing data to the LOT
        lot.fx_rate = Decimal(request.POST.get('fx_rate', '0'))
        lot.profit_margin = Decimal(request.POST.get('profit_margin', '0'))
        lot.final_price = Decimal(request.POST.get('final_price', '0'))
        lot.status = 'PRICED'
        lot.save()
        
        log_action(emp, 'CEO_LOCK_PRICE', f'Locked price for {lot.lot_name} at {lot.final_price} ETB', 'TenderLot', lot.id, tender.company)
        
        # Complete the specific T13_LOT Task
        task = Task.objects.filter(payload__tender_id=tender.id, code='T13_LOT', payload__lot_id=lot.id).first()
        if task:
            task.status = 'COMPLETED'
            task.save()
            
        # Check if ALL lots are now priced. If yes, trigger the next step.
        all_priced = all(l.status == 'PRICED' for l in tender.lots.all())
        
        if all_priced:
            # All lots are priced, move to CPO preparation (T14) for the Master Tender
            create_task(
                tender.company, 'T14', 'FIN',
                f'Prepare CPOs & Bank Letters: {tender.bid_title}',
                'All lots are priced. Prepare the CPOs for submission.',
                {'tender_id': tender.id}, reverse('tender_detail', args=[tender.id])
            )
            
        return redirect('dashboard')

    return render(request, 'ceo_lot_pricing.html', {'tender': tender, 'lot': lot})
