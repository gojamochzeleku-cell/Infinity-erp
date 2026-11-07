from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from decimal import Decimal
from .models import Tender, TenderLot
from .gemini import predict_winning_price

@login_required
def calculate_landed_cost(request, pk, lot_id):
    tender = get_object_or_404(Tender, pk=pk, company=getattr(request, 'company', None))
    lot = get_object_or_404(TenderLot, pk=lot_id, tender=tender)
    
    # Calculate total supplier cost from LotItems
    total_items_cost = sum(item.total_price for item in lot.items.all())
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'save_costs':
            lot.bank_charges = Decimal(request.POST.get('bank_charges', '0'))
            lot.tax_amount = Decimal(request.POST.get('tax_amount', '0'))
            
            # Landed Cost = Items + Transport (from master) + Bank + Tax
            lot.landed_cost = total_items_cost + tender.transport_cost + lot.bank_charges + lot.tax_amount
            lot.save()
            return redirect('ceo_lot_pricing', pk=tender.id, lot_id=lot.id)

    return render(request, 'landed_cost.html', {
        'tender': tender, 'lot': lot, 'total_items_cost': total_items_cost
    })

@login_required
def ask_ai_for_price(request, pk, lot_id):
    tender = get_object_or_404(Tender, pk=pk, company=getattr(request, 'company', None))
    lot = get_object_or_404(TenderLot, pk=lot_id, tender=tender)
    
    # Gather Historical Context
    won_tenders = Tender.objects.filter(company=tender.company, status='WON').count()
    lost_tenders = Tender.objects.filter(company=tender.company, status='LOST').count()
    
    historical_context = f"""
    Total Tenders Won: {won_tenders}
    Total Tenders Lost: {lost_tenders}
    Our Estimated Landed Cost for this lot: {lot.landed_cost} ETB
    """
    
    # Call AI
    ai_response = predict_winning_price(tender, historical_context)
    
    # Save the AI response to the Lot (we will add a field for this)
    from django.contrib import messages
    messages.success(request, f"AI Recommendation: {ai_response}")
    
    return redirect('ceo_lot_pricing', pk=tender.id, lot_id=lot.id)
