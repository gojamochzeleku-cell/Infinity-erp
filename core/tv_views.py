from django.shortcuts import render, redirect
from django.db.models import Count, Sum, Q
from datetime import date, timedelta
from .models import Company, Tender, Task, Correspondence, LegalCase, AuditLog, CashFlowEntry, Shipment, Employee, LeaveRequest

def tv_broadcast(request):
    companies = Company.objects.filter(is_active=True)
    today = date.today()
    warning_date = today + timedelta(days=7)

    # Speed setting from session, default 20s
    speed = int(request.session.get('tv_speed', 20))

    dashboards = []
    
    # 1. Company Specific Dashboards
    for company in companies:
        # Financials
        inflows = CashFlowEntry.objects.filter(company=company, flow_type='IN').aggregate(Sum('amount'))['amount__sum'] or 0
        outflows = CashFlowEntry.objects.filter(company=company, flow_type='OUT').aggregate(Sum('amount'))['amount__sum'] or 0
        balance = inflows - outflows
        
        # Logistics
        active_shipments = Shipment.objects.filter(company=company).exclude(current_status='WAREHOUSE').count()
        
        # Tenders
        won_value = Tender.objects.filter(company=company, status='WON').aggregate(Sum('total_cost'))['total_cost__sum'] or 0
        
        # HR
        on_leave = LeaveRequest.objects.filter(company=company, status='APPROVED', start_date__lte=today, end_date__gte=today).count()
        
        dashboards.append({
            'company': company,
            'company_name': company.name,
            'is_global': False,
            'active_tenders': Tender.objects.filter(company=company).exclude(status__in=['WON', 'LOST', 'COMPLETED']).count(),
            'overdue_tasks': Task.objects.filter(company=company, status='PENDING', created_at__date__lt=today - timedelta(days=2)).count(),
            'expiring_cpos': list(Tender.objects.filter(company=company, cpo_expiry__range=[today, warning_date]).values_list('serial_number', 'cpo_expiry')),
            'upcoming_cases': list(LegalCase.objects.filter(company=company, next_hearing_date__range=[today, warning_date]).values_list('title', 'next_hearing_date')),
            'overdue_letters': list(Correspondence.objects.filter(company=company, direction='OUTGOING', reply_deadline__lt=today).values_list('subject', flat=True)),
            'bottlenecks': list(Task.objects.filter(company=company, status='PENDING').values('assigned_to__role').annotate(count=Count('id')).order_by('-count')[:4]),
            'cash_balance': balance,
            'active_shipments': active_shipments,
            'won_value': won_value,
            'on_leave': on_leave,
        })

    # 2. Global Dashboard (Combined)
    global_balance = CashFlowEntry.objects.aggregate(Sum('amount'))['amount__sum'] or 0 # This is slightly wrong math for combined, let's fix below
    g_in = CashFlowEntry.objects.filter(flow_type='IN').aggregate(Sum('amount'))['amount__sum'] or 0
    g_out = CashFlowEntry.objects.filter(flow_type='OUT').aggregate(Sum('amount'))['amount__sum'] or 0

    dashboards.append({
        'company': None,
        'company_name': 'Infinity Investment Institute (Global)',
        'is_global': True,
        'active_tenders': Tender.objects.exclude(status__in=['WON', 'LOST', 'COMPLETED']).count(),
        'overdue_tasks': Task.objects.filter(status='PENDING', created_at__date__lt=today - timedelta(days=2)).count(),
        'expiring_cpos': list(Tender.objects.filter(cpo_expiry__range=[today, warning_date]).values_list('serial_number', 'cpo_expiry')),
        'upcoming_cases': list(LegalCase.objects.filter(next_hearing_date__range=[today, warning_date]).values_list('title', 'next_hearing_date')),
        'overdue_letters': list(Correspondence.objects.filter(direction='OUTGOING', reply_deadline__lt=today).values_list('subject', flat=True)),
        'bottlenecks': list(Task.objects.filter(status='PENDING').values('assigned_to__role').annotate(count=Count('id')).order_by('-count')[:4]),
        'cash_balance': g_in - g_out,
        'active_shipments': Shipment.objects.exclude(current_status='WAREHOUSE').count(),
        'won_value': Tender.objects.filter(status='WON').aggregate(Sum('total_cost'))['total_cost__sum'] or 0,
        'on_leave': LeaveRequest.objects.filter(status='APPROVED', start_date__lte=today, end_date__gte=today).count(),
    })

    # Ticker
    ticker_logs = AuditLog.objects.all().order_by('-created_at')[:15]
    ticker_messages = [f"{log.created_at.strftime('%H:%M')} - {log.actor.full_name if log.actor else 'System'}: {log.action} - {log.detail}" for log in ticker_logs if log.detail]

    return render(request, 'tv_broadcast.html', {
        'dashboards': dashboards,
        'ticker_messages': ticker_messages,
        'speed': speed
    })
