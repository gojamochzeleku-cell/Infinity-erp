from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.contrib import messages
from .models import Tender, Task, TenderLot, LotItem
from .chain import create_task
from .services import log_action

@login_required
def lot_assembly(request, pk, lot_id):
    tender = get_object_or_404(Tender, pk=pk, company=getattr(request, 'company', None))
    lot = get_object_or_404(TenderLot, pk=lot_id, tender=tender)
    emp = getattr(request.user, 'employee', None)
    
    if not emp or emp.role not in ('BIDOP', 'ADMIN'):
        return redirect('dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'save':
            if 'bo_technical_proposal' in request.FILES:
                lot.bo_technical_proposal = request.FILES['bo_technical_proposal']
            if 'bo_financial_proposal' in request.FILES:
                lot.bo_financial_proposal = request.FILES['bo_financial_proposal']
            if 'bo_compliance_matrix' in request.FILES:
                lot.bo_compliance_matrix = request.FILES['bo_compliance_matrix']
            lot.save()
            messages.success(request, f'{lot.lot_name} files uploaded successfully.')
            return redirect('lot_assembly', pk=tender.id, lot_id=lot.id)

        elif action == 'complete':
            lot.status = 'ASSEMBLED'
            lot.save()
            
            task = Task.objects.filter(payload__tender_id=tender.id, code='T10_LOT', payload__lot_id=lot.id).first()
            if task:
                task.status = 'COMPLETED'
                task.save()
                
            create_task(
                tender.company, 'T13_LOT', 'CEO',
                f'Set Price for {lot.lot_name} ({tender.serial_number})',
                f'Bid Operator assembled {lot.lot_name}. Please set FX and final price.',
                {'tender_id': tender.id, 'lot_id': lot.id}, reverse('tender_detail', args=[tender.id])
            )
            
            log_action(emp, 'ASSEMBLE_LOT', f'Assembled {lot.lot_name}', 'TenderLot', lot.id, tender.company)
            return redirect('dashboard')

    return render(request, 'lot_assembly.html', {'tender': tender, 'lot': lot})

@login_required
def manage_lot_items(request, pk, lot_id):
    tender = get_object_or_404(Tender, pk=pk, company=getattr(request, 'company', None))
    lot = get_object_or_404(TenderLot, pk=lot_id, tender=tender)
    emp = getattr(request.user, 'employee', None)

    if not emp or emp.role not in ('BIDOP', 'BIDM', 'ADMIN'):
        return redirect('dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add_item':
            name = request.POST.get('item_name')
            qty = request.POST.get('quantity', 1)
            unit = request.POST.get('unit', 'Pcs')
            brand = request.POST.get('proposed_brand', '')
            price = request.POST.get('unit_price', 0)
            
            if name:
                LotItem.objects.create(
                    lot=lot, item_name=name, quantity=qty, unit=unit,
                    proposed_brand=brand, unit_price=price
                )
            return redirect('manage_lot_items', pk=tender.id, lot_id=lot.id)

        elif action == 'delete_item':
            item_id = request.POST.get('item_id')
            LotItem.objects.filter(pk=item_id, lot=lot).delete()
            return redirect('manage_lot_items', pk=tender.id, lot_id=lot.id)

    items = lot.items.all()
    total_items_cost = sum(item.total_price for item in items)

    return render(request, 'manage_lot_items.html', {
        'tender': tender, 'lot': lot, 'items': items, 'total_items_cost': total_items_cost
    })
