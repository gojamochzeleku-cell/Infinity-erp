from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.utils import timezone
from datetime import timedelta, date
from django.db.models import Sum, Count, Q
from .models import Tender, CashFlowEntry, Task, LeaveRequest, Shipment, LegalCase

@login_required
def reports_center(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company or emp.role not in ('ADMIN', 'CEO', 'GM', 'VGM', 'FIN', 'BIDM'):
        return redirect('dashboard')

    period = request.GET.get('period', 'monthly')
    today = date.today()

    # 1. Calculate Date Range
    if period == 'daily':
        start_date = today
        end_date = today
        period_name = "Today"
    elif period == 'weekly':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
        period_name = "This Week"
    elif period == 'quarterly':
        current_quarter = (today.month - 1) // 3 + 1
        start_month = 3 * current_quarter - 2
        start_date = date(today.year, start_month, 1)
        period_name = f"Q{current_quarter} {today.year}"
        # Calculate end of quarter
        if start_month + 3 > 12:
            end_date = date(today.year, 12, 31)
        else:
            end_date = date(today.year, start_month + 3, 1) - timedelta(days=1)
    else: # Default Monthly
        start_date = today.replace(day=1)
        if today.month == 12:
            end_date = date(today.year, 12, 31)
        else:
            end_date = date(today.year, today.month + 1, 1) - timedelta(days=1)
        period_name = today.strftime("%B %Y")

    # 2. Financial Metrics
    inflows = CashFlowEntry.objects.filter(company=company, flow_type='IN', date__range=[start_date, end_date]).aggregate(Sum('amount'))['amount__sum'] or 0
    outflows = CashFlowEntry.objects.filter(company=company, flow_type='OUT', date__range=[start_date, end_date]).aggregate(Sum('amount'))['amount__sum'] or 0
    net_cash = inflows - outflows

    # 3. Tender Metrics
    tenders_won = Tender.objects.filter(company=company, status='WON', updated_at__date__range=[start_date, end_date]).count()
    tenders_lost = Tender.objects.filter(company=company, status='LOST', updated_at__date__range=[start_date, end_date]).count()
    tenders_submitted = Tender.objects.filter(company=company, submission_status='Submitted', updated_at__date__range=[start_date, end_date]).count()
    
    total_finished = tenders_won + tenders_lost
    win_rate = round((tenders_won / total_finished * 100), 1) if total_finished > 0 else 0

    # 4. Operational Metrics
    tasks_completed = Task.objects.filter(company=company, status='COMPLETED', completed_at__date__range=[start_date, end_date]).count()
    active_shipments = Shipment.objects.filter(company=company).exclude(current_status='WAREHOUSE').count()
    
    # 5. HR Metrics
    leaves_taken = LeaveRequest.objects.filter(company=company, status='APPROVED', start_date__range=[start_date, end_date]).count()

    context = {
        'period': period,
        'period_name': period_name,
        'start_date': start_date,
        'end_date': end_date,
        'inflows': inflows,
        'outflows': outflows,
        'net_cash': net_cash,
        'tenders_won': tenders_won,
        'tenders_lost': tenders_lost,
        'tenders_submitted': tenders_submitted,
        'win_rate': win_rate,
        'tasks_completed': tasks_completed,
        'active_shipments': active_shipments,
        'leaves_taken': leaves_taken,
    }
    return render(request, 'reports_center.html', context)
