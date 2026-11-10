from django.db import models, transaction
from django.http import HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from .models import Product, StockTransaction
from .services import log_action


def _inventory_access(request):
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

def inventory_dashboard(request):
    company, emp, roles = _inventory_access(request)
    if company is None:
        return redirect('select_company')

    if not roles.intersection({'PROC', 'LOG', 'ADMIN', 'CEO', 'GM', 'BIDM'}):
        return HttpResponseForbidden("You are not authorized to view inventory.")

    products = Product.objects.filter(company=company).order_by('name')
    total_items = sum(p.current_stock for p in products)
    low_stock = products.filter(current_stock__lte=models.F('min_stock_level'))

    if request.method == 'POST':
        if not roles.intersection({'PROC', 'ADMIN'}):
            return HttpResponseForbidden(
                "Only Procurement or company Admin can add products."
            )

        name = (request.POST.get('name') or '').strip()
        sku = (request.POST.get('sku') or '').strip()
        cat = (request.POST.get('category') or '').strip()
        loc = (request.POST.get('location') or '').strip()

        try:
            initial_stock = int(request.POST.get('initial_stock', '0'))
            min_stock = int(request.POST.get('min_stock', '5'))
            if not name or initial_stock < 0 or min_stock < 0:
                raise ValueError
        except (TypeError, ValueError):
            return render(request, 'inv_dashboard.html', {
                'products': products,
                'total_items': total_items,
                'low_stock_count': low_stock.count(),
                'can_add_product': True,
                'can_adjust_stock': True,
                'error': 'Enter a product name and valid non-negative stock quantities.'
            })

        with transaction.atomic():
            product = Product.objects.create(
                company=company, name=name, sku=sku, category=cat,
                min_stock_level=min_stock, location=loc,
                current_stock=initial_stock
            )
            if initial_stock > 0:
                StockTransaction.objects.create(
                    product=product, trans_type='IN', quantity=initial_stock,
                    notes='Initial Stock Setup', created_by=emp
                )

        return redirect('inventory_dashboard')

    return render(request, 'inv_dashboard.html', {
        'products': products,
        'total_items': total_items,
        'low_stock_count': low_stock.count(),
        'can_add_product': bool(roles.intersection({'PROC', 'ADMIN'})),
        'can_adjust_stock': bool(roles.intersection({'PROC', 'ADMIN'}))
    })

@login_required

def inventory_adjust(request, pk):
    company, emp, roles = _inventory_access(request)
    if company is None:
        return redirect('select_company')
    if not roles.intersection({'PROC', 'ADMIN'}):
        return HttpResponseForbidden(
            "Only Procurement or company Admin can adjust stock."
        )

    product = get_object_or_404(Product, pk=pk, company=company)

    if request.method == 'POST':
        trans_type = (request.POST.get('trans_type') or '').strip()
        notes = (request.POST.get('notes') or '').strip()

        try:
            qty = int(request.POST.get('quantity', ''))
            if trans_type not in {'IN', 'OUT', 'ADJUST'} or qty < 0:
                raise ValueError
            if trans_type in {'IN', 'OUT'} and qty == 0:
                raise ValueError
            if not notes:
                raise ValueError
        except (TypeError, ValueError):
            return render(request, 'inv_adjust.html', {
                'product': product,
                'error': 'Enter a valid quantity, transaction type and reason. IN/OUT quantity must be greater than zero.'
            })

        with transaction.atomic():
            product = Product.objects.select_for_update().get(
                pk=pk, company=company
            )
            old_stock = product.current_stock

            if trans_type == 'IN':
                product.current_stock = old_stock + qty
            elif trans_type == 'OUT':
                if old_stock < qty:
                    return render(request, 'inv_adjust.html', {
                        'product': product,
                        'error': 'Not enough stock available!'
                    })
                product.current_stock = old_stock - qty
            else:
                product.current_stock = qty

            delta = product.current_stock - old_stock
            product.save(update_fields=['current_stock'])

            StockTransaction.objects.create(
                product=product,
                trans_type=trans_type,
                quantity=delta,
                notes=notes,
                created_by=emp
            )

        return redirect('inventory_dashboard')

    return render(request, 'inv_adjust.html', {'product': product})
