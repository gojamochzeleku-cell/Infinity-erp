from django.db import models
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from .models import Product, StockTransaction
from .services import log_action

@login_required
def inventory_dashboard(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')

    products = Product.objects.filter(company=company).order_by('name')
    
    # Calculate total stock value or just total items
    total_items = sum(p.current_stock for p in products)
    low_stock = products.filter(current_stock__lte=models.F('min_stock_level'))

    if request.method == 'POST':
        name = request.POST.get('name')
        sku = request.POST.get('sku', '')
        cat = request.POST.get('category', '')
        min_stock = request.POST.get('min_stock', 5)
        loc = request.POST.get('location', '')
        initial_stock = request.POST.get('initial_stock', 0)

        product = Product.objects.create(
            company=company, name=name, sku=sku, category=cat,
            min_stock_level=min_stock, location=loc, current_stock=initial_stock
        )
        
        if int(initial_stock) > 0:
            StockTransaction.objects.create(
                product=product, trans_type='IN', quantity=initial_stock,
                notes='Initial Stock Setup', created_by=request.user.employee
            )
            
        return redirect('inventory_dashboard')

    return render(request, 'inv_dashboard.html', {
        'products': products, 'total_items': total_items, 'low_stock_count': low_stock.count()
    })

@login_required
def inventory_adjust(request, pk):
    product = get_object_or_404(Product, pk=pk)
    
    if request.method == 'POST':
        qty = int(request.POST.get('quantity'))
        trans_type = request.POST.get('trans_type')
        notes = request.POST.get('notes', '')
        
        if trans_type == 'IN':
            product.current_stock += qty
        elif trans_type == 'OUT':
            if product.current_stock >= qty:
                product.current_stock -= qty
            else:
                return render(request, 'inv_adjust.html', {'product': product, 'error': 'Not enough stock available!'})
        elif trans_type == 'ADJUST':
            product.current_stock = qty
            
        product.save()
        StockTransaction.objects.create(
            product=product, trans_type=trans_type, quantity=qty,
            notes=notes, created_by=request.user.employee
        )
        return redirect('inventory_dashboard')
        
    return render(request, 'inv_adjust.html', {'product': product})
