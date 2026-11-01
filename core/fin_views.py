from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db.models import Sum
from .models import CashFlowEntry, CompanySetting, Tender, Employee
from .services import log_action
from decimal import Decimal

@login_required
def cashflow_dashboard(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company or emp.role not in ('FIN', 'CEO', 'ADMIN', 'GM'):
        return redirect('dashboard')

    entries = CashFlowEntry.objects.filter(company=company)
    
    inflows = entries.filter(flow_type='IN').aggregate(Sum('amount'))['amount__sum'] or 0
    outflows = entries.filter(flow_type='OUT').aggregate(Sum('amount'))['amount__sum'] or 0
    balance = inflows - outflows

    if request.method == 'POST':
        # Manual entry (Rent, Salary, etc)
        flow_type = request.POST.get('flow_type')
        category = request.POST.get('category')
        amount = Decimal(request.POST.get('amount', '0'))
        desc = request.POST.get('description')
        
        CashFlowEntry.objects.create(
            company=company, flow_type=flow_type, category=category,
            amount=amount, description=desc, created_by=emp
        )
        return redirect('cashflow_dashboard')

    return render(request, 'fin_cashflow.html', {
        'entries': entries[:50], 'inflows': inflows, 'outflows': outflows, 'balance': balance
    })

@login_required
def guarantee_tracker(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company or emp.role not in ('FIN', 'CEO', 'ADMIN', 'GM', 'LEGAL'):
        return redirect('dashboard')

    # Get all tenders that have bonds/CPOs
    tenders = Tender.objects.filter(company=company).exclude(cpo_expiry__isnull=True, performance_bond_expiry__isnull=True)
    
    total_cpo = tenders.aggregate(Sum('cpo_amount'))['cpo_amount__sum'] or 0
    return render(request, 'fin_guarantees.html', {'tenders': tenders, 'total_cpo': total_cpo, 'today': timezone.now().date()})

@login_required
def finance_settings(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company or emp.role not in ('CEO', 'ADMIN'):
        return redirect('dashboard')

    setting, _ = CompanySetting.objects.get_or_create(company=company)

    if request.method == 'POST':
        new_thresh = request.POST.get('threshold')
        if new_thresh:
            setting.payment_approval_threshold = Decimal(new_thresh)
            setting.save()
            log_action(emp, 'UPDATE_FIN_SETTINGS', f'Changed payment threshold to {new_thresh}', 'CompanySetting', setting.id, company)
            return redirect('finance_settings')

    return render(request, 'fin_settings.html', {'setting': setting})
