from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count
from .models import Tender, TenderLot, CashFlowEntry

@login_required
def profitability_dashboard(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    
    if not emp or not company or emp.role not in ('ADMIN', 'CEO', 'GM', 'FIN'):
        return redirect('dashboard')

    # 1. Fetch all completed tenders
    completed_tenders = Tender.objects.filter(company=company, status='COMPLETED')
    
    project_data = []
    total_revenue = 0
    total_cost = 0
    
    for t in completed_tenders:
        # Revenue is the sum of final prices of all lots
        revenue = sum(lot.final_price for lot in t.lots.all() if lot.final_price > 0)
        # Cost is the sum of landed costs of all lots
        cost = sum(lot.landed_cost for lot in t.lots.all() if lot.landed_cost > 0)
        
        # If no lot data exists, fallback to the old tender final_price
        if revenue == 0 and t.final_price > 0:
            revenue = t.final_price
        if cost == 0 and t.total_cost > 0:
            cost = t.total_cost
            
        profit = revenue - cost
        margin = (profit / revenue * 100) if revenue > 0 else 0
        
        project_data.append({
            'tender': t,
            'revenue': revenue,
            'cost': cost,
            'profit': profit,
            'margin': round(margin, 1)
        })
        
        total_revenue += revenue
        total_cost += cost

    total_profit = total_revenue - total_cost
    overall_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0

    # 2. Purchaser Performance (Which organizations give us the most money?)
    purchaser_stats = completed_tenders.values('purchaser_name').annotate(
        total_revenue=Sum('lots__final_price'),
        count=Count('id')
    ).order_by('-total_revenue')[:5]

    # Sort projects by highest profit
    project_data.sort(key=lambda x: x['profit'], reverse=True)

    return render(request, 'profit_dashboard.html', {
        'project_data': project_data,
        'total_revenue': total_revenue,
        'total_cost': total_cost,
        'total_profit': total_profit,
        'overall_margin': round(overall_margin, 1),
        'purchaser_stats': purchaser_stats
    })
