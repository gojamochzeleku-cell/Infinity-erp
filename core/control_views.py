from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db.models import Count
from datetime import date, timedelta
from .models import Tender, Task, Correspondence, LegalCase, Employee
from .alerts import check_critical_alerts
from dateutil import parser as date_parser

@login_required
def tv_dashboard(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')
    
    # Trigger alerts check when TV dashboard loads
    emp = getattr(request.user, 'employee', None)
    if emp and emp.role in ('ADMIN', 'CEO', 'GM'):
        check_critical_alerts(company, emp)

    today = date.today()
    warning_date = today + timedelta(days=7)

    context = {
        'active_tenders': Tender.objects.filter(company=company).exclude(status__in=['WON', 'LOST', 'COMPLETED']).count(),
        'overdue_tasks': Task.objects.filter(company=company, status='PENDING', created_at__date__lt=today - timedelta(days=2)).count(),
        
        # Expiring soon
        'expiring_cpos': Tender.objects.filter(company=company, cpo_expiry__range=[today, warning_date]),
        'expiring_bonds': Tender.objects.filter(company=company, performance_bond_expiry__range=[today, warning_date]),
        
        # Legal & Letters
        'upcoming_cases': LegalCase.objects.filter(company=company, next_hearing_date__range=[today, warning_date]),
        'overdue_letters': Correspondence.objects.filter(company=company, direction='OUTGOING', reply_deadline__lt=today),
        
        # Bottlenecks
        'bottlenecks': Task.objects.filter(company=company, status='PENDING').values('assigned_to__role').annotate(count=Count('id')).order_by('-count')[:5]
    }
    return render(request, 'tv_dashboard.html', context)

@login_required
def legal_list(request):
    company = getattr(request, 'company', None)
    cases = LegalCase.objects.filter(company=company) if company else []
    if request.method == 'POST':
        emp = getattr(request.user, 'employee', None)
        title = request.POST.get('title')
        desc = request.POST.get('description')
        hearing = request.POST.get('next_hearing_date')
        lawyer_id = request.POST.get('assigned_lawyer')
        
        lawyer = Employee.objects.filter(id=lawyer_id).first() if lawyer_id else None
        LegalCase.objects.create(
            company=company, title=title, description=desc,
            next_hearing_date=date_parser.parse(hearing).date() if hearing else None,
            assigned_lawyer=lawyer
        )
        return redirect('legal_list')
        
    employees = Employee.objects.all()
    tenders = Tender.objects.filter(company=company)
    return render(request, 'legal_list.html', {'cases': cases, 'employees': employees, 'tenders': tenders})

@login_required
def assign_bid_tracker(request, pk):
    tender = Tender.objects.filter(pk=pk).first()
    if request.method == 'POST' and tender:
        tracker_id = request.POST.get('bid_tracker')
        if tracker_id:
            tender.bid_tracker = Employee.objects.filter(id=tracker_id).first()
            tender.save()
            return redirect('tender_detail', pk=tender.id)
    employees = Employee.objects.all()
    return render(request, 'assign_tracker.html', {'tender': tender, 'employees': employees})
