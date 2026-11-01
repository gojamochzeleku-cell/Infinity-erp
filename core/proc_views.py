from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from .models import Supplier, Shipment, InventoryItem, Tender
from dateutil import parser as date_parser

@login_required
def supplier_list(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')
    suppliers = Supplier.objects.filter(company=company).order_by('-rating')

    if request.method == 'POST':
        Supplier.objects.create(
            company=company, name=request.POST.get('name'),
            contact_person=request.POST.get('contact_person'),
            phone=request.POST.get('phone'), email=request.POST.get('email')
        )
        return redirect('supplier_list')

    return render(request, 'supplier_list.html', {'suppliers': suppliers})

@login_required
def shipment_list(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')
    
    shipments = Shipment.objects.filter(company=company).order_by('-updated_at')
    return render(request, 'shipment_list.html', {'shipments': shipments})

@login_required
def shipment_update(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)
    if request.method == 'POST':
        shipment.current_status = request.POST.get('status')
        shipment.vessel_name = request.POST.get('vessel_name', shipment.vessel_name)
        shipment.bill_of_lading = request.POST.get('bill_of_lading', shipment.bill_of_lading)
        eta = request.POST.get('eta')
        if eta:
            shipment.eta = date_parser.parse(eta).date()
        shipment.save()
        return redirect('shipment_list')
    return render(request, 'shipment_update.html', {'shipment': shipment})

@login_required
def inventory_check(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)
    if request.method == 'POST':
        item_name = request.POST.get('item_name')
        qty_exp = request.POST.get('qty_expected')
        qty_rec = request.POST.get('qty_received')
        condition = request.POST.get('condition')
        
        InventoryItem.objects.create(
            shipment=shipment, item_name=item_name,
            qty_expected=qty_exp, qty_received=qty_rec,
            condition=condition, notes=request.POST.get('notes', '')
        )
        return redirect('inventory_check', pk=shipment.pk)
        
    return render(request, 'inventory_check.html', {'shipment': shipment})
