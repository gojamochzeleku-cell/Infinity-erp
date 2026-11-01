from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db.models import Sum, Count
from .models import Tender, Task, AuditLog

@login_required
def management_dashboard(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    
    # Only allow management roles to see this dashboard
    if not emp or not company or emp.role not in ('ADMIN', 'CEO', 'GM', 'VGM'):
        return redirect('dashboard')

    tenders = Tender.objects.filter(company=company)
    
    # 1. Financials
    financials = tenders.aggregate(
        total_cost=Sum('total_cost'),
        total_duty=Sum('duty_tax_amount'),
        total_payments=Sum('payment_amount')
    )

    # 2. Pipeline Stats
    stats = {
        'total': tenders.count(),
        'won': tenders.filter(status='WON').count(),
        'lost': tenders.filter(status__in=['LOST', 'NO_GO', 'CEO_REJECTED']).count(),
        'active': tenders.exclude(status__in=['WON', 'LOST', 'NO_GO', 'CEO_REJECTED', 'COMPLETED', 'DRAFT']).count(),
        'completed': tenders.filter(status='COMPLETED').count(),
    }

    # 3. Bottlenecks (Pending tasks by role)
    bottlenecks = Task.objects.filter(company=company).exclude(status__in=['COMPLETED', 'CANCELLED', 'REJECTED']).values('assigned_to__role').annotate(count=Count('id')).order_by('-count')

    # 3.5 Loss Analysis
    lost_tenders = tenders.filter(status='LOST').exclude(loss_reason='').order_by('-updated_at')[:5]

    # 4. Recent Activity (Audit Log)
    recent_activity = AuditLog.objects.filter(company=company).order_by('-created_at')[:15]

    context = {
        'stats': stats,
        'financials': financials,
        'bottlenecks': bottlenecks,
        'recent_activity': recent_activity,
        'lost_tenders': lost_tenders,
    }
    return render(request, 'mgmt_dashboard.html', context)
