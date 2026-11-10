from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from .models import Supplier, Shipment, InventoryItem, Tender
from dateutil import parser as date_parser


def _procurement_access(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or emp is None or not emp.is_active
        or company is None or not company.is_active
        or membership is None or not membership.is_active
        or membership.employee_id != emp.id
        or membership.company_id != company.id
    ):
        return None, None, set()

    roles = set(membership.roles.values_list('role', flat=True))
    return company, emp, roles

@login_required
def supplier_list(request):
    company, emp, roles = _procurement_access(request)

    if company is None:
        return redirect('select_company')

    allowed_view_roles = {'PROC', 'LOG', 'ADMIN', 'CEO', 'GM', 'BIDM'}
    if not roles.intersection(allowed_view_roles):
        return HttpResponseForbidden(
            "You are not authorized to view suppliers."
        )

    suppliers = Supplier.objects.filter(
        company=company
    ).order_by('-rating')

    can_add_supplier = bool(roles.intersection({'PROC', 'ADMIN'}))

    if request.method == 'POST':
        if not can_add_supplier:
            return HttpResponseForbidden(
                "Only Procurement or company Admin can add suppliers."
            )

        name = (request.POST.get('name') or '').strip()
        if not name:
            return render(request, 'supplier_list.html', {
                'suppliers': suppliers,
                'can_add_supplier': True,
                'error': 'Supplier company name is required.',
            })

        Supplier.objects.create(
            company=company,
            name=name,
            contact_person=(request.POST.get('contact_person') or '').strip(),
            phone=(request.POST.get('phone') or '').strip(),
            email=(request.POST.get('email') or '').strip(),
        )
        return redirect('supplier_list')

    return render(request, 'supplier_list.html', {
        'suppliers': suppliers,
        'can_add_supplier': can_add_supplier,
    })

@login_required

def shipment_list(request):
    company, emp, roles = _procurement_access(request)
    if company is None:
        return redirect('select_company')
    if not roles.intersection({'PROC', 'LOG', 'ADMIN', 'CEO', 'GM', 'BIDM'}):
        return HttpResponseForbidden("You are not authorized to view shipments.")

    shipments = Shipment.objects.filter(company=company).order_by('-updated_at')
    return render(request, 'shipment_list.html', {
        'shipments': shipments,
        'can_update_shipment': bool(roles.intersection({'LOG', 'ADMIN'})),
        'can_inspect_shipment': bool(roles.intersection({'PROC', 'LOG', 'ADMIN'})),
    })

@login_required

def shipment_update(request, pk):
    company, emp, roles = _procurement_access(request)
    if company is None:
        return redirect('select_company')
    if not roles.intersection({'LOG', 'ADMIN'}):
        return HttpResponseForbidden(
            "Only Logistics or company Admin can update shipments."
        )

    shipment = get_object_or_404(Shipment, pk=pk, company=company)

    if request.method == 'POST':
        status = (request.POST.get('status') or '').strip()
        vessel = (request.POST.get('vessel_name') or '').strip()
        bill = (request.POST.get('bill_of_lading') or '').strip()
        eta_value = (request.POST.get('eta') or '').strip()

        valid_statuses = {choice[0] for choice in Shipment.STATUS_CHOICES}
        if status not in valid_statuses:
            return render(request, 'shipment_update.html', {
                'shipment': shipment,
                'error': 'Select a valid shipment status.'
            })

        try:
            eta = date_parser.parse(eta_value).date() if eta_value else None
        except (ValueError, TypeError, OverflowError):
            return render(request, 'shipment_update.html', {
                'shipment': shipment,
                'error': 'Enter a valid estimated arrival date.'
            })

        with transaction.atomic():
            shipment = Shipment.objects.select_for_update().get(
                pk=pk, company=company
            )
            shipment.current_status = status
            shipment.vessel_name = vessel
            shipment.bill_of_lading = bill
            shipment.eta = eta
            shipment.save()

        return redirect('shipment_list')

    return render(request, 'shipment_update.html', {'shipment': shipment})

@login_required

def inventory_check(request, pk):
    company, emp, roles = _procurement_access(request)
    if company is None:
        return redirect('select_company')
    if not roles.intersection({'PROC', 'LOG', 'ADMIN'}):
        return HttpResponseForbidden(
            "You are not authorized to inspect received goods."
        )

    shipment = get_object_or_404(Shipment, pk=pk, company=company)

    if request.method == 'POST':
        item_name = (request.POST.get('item_name') or '').strip()
        notes = (request.POST.get('notes') or '').strip()

        try:
            qty_exp = int(request.POST.get('qty_expected', ''))
            qty_rec = int(request.POST.get('qty_received', ''))
            qty_damaged = int(request.POST.get('qty_damaged', '0'))

            if (
                not item_name or qty_exp <= 0 or qty_rec <= 0
                or qty_damaged < 0 or qty_damaged > qty_rec
            ):
                raise ValueError
        except (TypeError, ValueError):
            return render(request, 'inventory_check.html', {
                'shipment': shipment,
                'can_inspect': True,
                'error': 'Enter an item name and valid quantities. Received quantity must be positive and damaged quantity cannot exceed received quantity.'
            })

        if qty_damaged == 0:
            condition = 'GOOD'
        elif qty_damaged == qty_rec:
            condition = 'DAMAGED'
        else:
            condition = 'MIXED'

        with transaction.atomic():
            shipment = Shipment.objects.select_for_update().get(
                pk=pk, company=company
            )
            InventoryItem.objects.create(
                shipment=shipment,
                item_name=item_name,
                qty_expected=qty_exp,
                qty_received=qty_rec,
                qty_damaged=qty_damaged,
                condition=condition,
                notes=notes
            )

        return redirect('inventory_check', pk=shipment.pk)

    return render(request, 'inventory_check.html', {
        'shipment': shipment,
        'can_inspect': True,
    })
