from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from datetime import date, timedelta
from .models import Tender, CashFlowEntry

@login_required
def cash_flow_forecast(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    
    if not emp or not company or emp.role not in ('ADMIN', 'CEO', 'GM', 'FIN'):
        return redirect('dashboard')

    today = date.today()

    # 1. Current Actual Cash Balance
    inflows = CashFlowEntry.objects.filter(company=company, flow_type='IN').aggregate(Sum('amount'))['amount__sum'] or 0
    outflows = CashFlowEntry.objects.filter(company=company, flow_type='OUT').aggregate(Sum('amount'))['amount__sum'] or 0
    current_cash = inflows - outflows

    # 2. Active Pipeline (Money waiting to move)
    active_tenders = Tender.objects.filter(company=company).exclude(status__in=['WON', 'LOST', 'COMPLETED', 'DRAFT'])
    
    pending_outflow = 0 # Costs we expect to pay
    pending_inflow = 0  # Revenue we expect to collect
    
    for t in active_tenders:
        for lot in t.lots.all():
            pending_outflow += lot.landed_cost
            if lot.status == 'PRICED':
                pending_inflow += lot.final_price

    # 3. Receivables Aging (Customers who owe us money)
    # A tender is in 'WON' status but not 'COMPLETED' (meaning GRN is uploaded but payment not collected)
    receivables = Tender.objects.filter(company=company, status='WON')
    overdue_receivables = []
    for r in receivables:
        # If the tender has been won but not completed for more than 30 days, flag it
        if r.updated_at.date() < (today - timedelta(days=30)):
            expected_amount = sum(lot.final_price for lot in r.lots.all() if lot.final_price > 0)
            overdue_receivables.append({'tender': r, 'amount': expected_amount})

    # 4. Projected Cash Flow (Current + Inflows - Outflows)
    projected_cash = current_cash + pending_inflow - pending_outflow

    # 5. Simple Health Score (0-100)
    health_score = 50
    if current_cash > 0: health_score += 20
    if overdue_receivables: health_score -= 20
    if projected_cash > current_cash: health_score += 30
    health_score = max(0, min(100, health_score))

    return render(request, 'forecast_dashboard.html', {
        'current_cash': current_cash,
        'pending_inflow': pending_inflow,
        'pending_outflow': pending_outflow,
        'projected_cash': projected_cash,
        'health_score': health_score,
        'overdue_receivables': overdue_receivables,
        'active_tenders_count': active_tenders.count()
    })
