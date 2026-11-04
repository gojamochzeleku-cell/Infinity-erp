from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from datetime import timedelta
from django.db.models import Sum, Count
from .models import CashFlowEntry
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

    # 5. CHART DATA GENERATION
    from datetime import date
    from django.db.models.functions import TruncMonth
    
    # Tender Status Doughnut
    tender_data = [stats['won'], stats['lost'], stats['active']]
    
    # Cash Flow Bar Chart (Last 6 Months)
    six_months_ago = date.today() - timedelta(days=180)
    monthly_flows = CashFlowEntry.objects.filter(company=company, date__gte=six_months_ago).annotate(month=TruncMonth('date')).values('month', 'flow_type').annotate(total=Sum('amount')).order_by('month')
    
    flow_labels = []
    flow_in = []
    flow_out = []
    
    # Process the flow data into lists for the chart
    temp_data = {}
    for flow in monthly_flows:
        month_str = flow['month'].strftime('%b %Y')
        if month_str not in temp_data:
            temp_data[month_str] = {'IN': 0, 'OUT': 0}
        temp_data[month_str][flow['flow_type']] += float(flow['total'])
        
    for month, data in temp_data.items():
        flow_labels.append(month)
        flow_in.append(data['IN'])
        flow_out.append(data['OUT'])

    # Expense Breakdown Pie Chart
    expense_data = CashFlowEntry.objects.filter(company=company, flow_type='OUT').values('category').annotate(total=Sum('amount')).order_by('-total')
    expense_labels = []
    expense_values = []
    for exp in expense_data:
        # Get human readable category name
        cat_name = dict(CashFlowEntry.CATEGORY_CHOICES).get(exp['category'], exp['category'])
        expense_labels.append(cat_name)
        expense_values.append(float(exp['total']))

    context = {
        'stats': stats,
        'financials': financials,
        'bottlenecks': bottlenecks,
        'recent_activity': recent_activity,
        'lost_tenders': lost_tenders,
        'tender_data': tender_data,
        'flow_labels': flow_labels,
        'flow_in': flow_in,
        'flow_out': flow_out,
        'expense_labels': expense_labels,
        'expense_values': expense_values,
    }
    return render(request, 'mgmt_dashboard.html', context)
