from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.db.models import Q
from decimal import Decimal

from .models import Company, Task, Notification, Tender, LeaveRequest, ExpenseRequest
from .services import log_action
from .chain import register_document, advance_tender_chain, process_cost_request

class InfinityLoginView(LoginView):
    template_name = 'login.html'
    redirect_authenticated_user = True

def home(request):
    if request.user.is_authenticated:
        return redirect('select_company')
    return redirect('login')

@login_required
def select_company(request):
    companies = Company.objects.filter(is_active=True)
    if request.method == 'POST':
        cid = request.POST.get('company_id')
        if cid:
            request.session['company_id'] = int(cid)
            return redirect('dashboard')
    return render(request, 'select_company.html', {'companies': companies})

@login_required
def dashboard(request):
    emp = getattr(request.user, 'employee', None)
    if emp is None:
        return render(request, 'no_profile.html')
    company = getattr(request, 'company', None)
    
    qs = Task.objects.filter(
        Q(assigned_to=emp) | Q(assigned_to__role=emp.role),
        company=company
    ).distinct().exclude(status__in=['COMPLETED', 'CANCELLED'])
    
    if company is not None:
        qs = qs.filter(company=company)
        
    tasks = list(qs.order_by('-created_at')[:100])
    
    done = Task.objects.filter(
        Q(assigned_to=emp) | Q(assigned_to__role=emp.role)
    ).distinct().filter(status='COMPLETED')
    if company is not None:
        done = done.filter(company=company)
        
    stats = {
        'pending': sum(1 for t in tasks if t.status == 'PENDING'),
        'in_progress': sum(1 for t in tasks if t.status == 'IN_PROGRESS'),
        'completed': done.count(),
    }

    # EXECUTIVE ROUTING
    if emp.role in ('CEO', 'GM', 'ADMIN'):
        # Filter for tasks that strictly require Executive approval/action
        approval_codes = ['T03', 'T06', 'T12', 'T16', 'I03', 'I08', 'I16', 'CEO_EXPENSE', 'GM_AGENT_APPROVED', 'GM_LEAVE']
        
        # We want all pending tasks assigned to them, but we will categorize them
        executive_approvals = [t for t in tasks if t.code in approval_codes]
        
        # Get active tenders for the tracker
        active_tenders = Tender.objects.filter(company=company).exclude(status__in=['WON', 'LOST', 'COMPLETED', 'DRAFT']).order_by('-updated_at')[:5]
        
        # Get recent audit logs for tracking
        from .models import AuditLog
        recent_activity = AuditLog.objects.filter(company=company).order_by('-created_at')[:5]

        return render(request, 'dashboard_executive.html', {
            'approvals': executive_approvals,
            'other_tasks': [t for t in tasks if t.code not in approval_codes],
            'stats': stats,
            'active_tenders': active_tenders,
            'recent_activity': recent_activity,
            'today': timezone.now().date()
        })

    return render(request, 'dashboard.html', {'tasks': tasks, 'stats': stats, 'today': timezone.now().date()})

@login_required
def task_detail(request, pk):
    emp = getattr(request.user, 'employee', None)
    task = get_object_or_404(Task, pk=pk)
    company = getattr(request, 'company', None)
    
    # STRICT COMPANY ISOLATION
    if task.company != company:
        return redirect('dashboard')
        
    if emp is None or (task.assigned_to_id != emp.id and task.assigned_to.role != emp.role and not emp.is_group_level):
        return redirect('dashboard')
    
    tender = None
    if task.payload and 'tender_id' in task.payload:
        tender = Tender.objects.filter(pk=task.payload['tender_id']).first()
        
    from .models import Product
    all_products = Product.objects.filter(company=task.company)
    return render(request, 'task_detail.html', {'task': task, 'tender': tender, 'all_products': all_products})

@login_required
def task_action(request, pk):
    emp = getattr(request.user, 'employee', None)
    task = get_object_or_404(Task, pk=pk)
    company = getattr(request, 'company', None)
    
    # STRICT COMPANY ISOLATION
    if task.company != company:
        return redirect('dashboard')
        
    if emp is None or (task.assigned_to_id != emp.id and task.assigned_to.role != emp.role and not emp.is_group_level):
        return redirect('dashboard')
        
    if request.method == 'POST':
        act = request.POST.get('action', '')
        note = request.POST.get('note', '').strip()
        
        if task.code == 'SUPPLY_REQ':
            if act == 'complete':
                task.status = 'COMPLETED'
                task.save()
                return redirect('dashboard')

        elif task.code == 'FIN_PAYROLL':
            from .models import PayrollRecord, CashFlowEntry
            payroll_id = task.payload.get('payroll_id')
            payroll = PayrollRecord.objects.filter(pk=payroll_id).first()
            if payroll and act == 'complete':
                payroll.status = 'PAID'
                payroll.save()
                CashFlowEntry.objects.create(
                    company=task.company, flow_type='OUT', category='SALARY',
                    amount=payroll.total_amount, description=f'Payroll for {payroll.month} {payroll.year}',
                    created_by=emp
                )
                task.status = 'COMPLETED'
                task.save()
                return redirect('dashboard')

        elif task.code in ('T02', 'T03'):
            tender = Tender.objects.filter(pk=task.payload.get('tender_id')).first()
            if tender:
                if act in ('GO', 'NO_GO', 'APPROVED', 'REJECTED'):
                    advance_tender_chain(request, tender, task, act, note)
                    return redirect('dashboard')
                elif act in ('complete', 'start'):
                    # Failsafe: if they somehow click the generic button, just complete it
                    task.status = 'COMPLETED'
                    task.save()
                    return redirect('dashboard')
                
        elif task.code == 'T04':
            tender = Tender.objects.filter(pk=task.payload.get('tender_id')).first()
            if tender and act == 'SUBMIT_COST':
                try:
                    tc = Decimal(request.POST.get('transport_cost', '0'))
                except:
                    tc = Decimal('0')
                tender.transport_cost = tc
                tender.transport_from = request.POST.get('transport_from', '')
                tender.transport_to = request.POST.get('transport_to', '')
                tender.transport_reason = request.POST.get('transport_reason', '')
                tender.total_cost = tender.bid_document_cost_amount + tender.transport_cost
                tender.save()
                process_cost_request(tender, task, 'SUBMIT_COST', note)
                return redirect('dashboard')
                
        elif task.code in ('T05', 'T06'):
            tender = Tender.objects.filter(pk=task.payload.get('tender_id')).first()
            if tender:
                if task.code == 'T05' and act == 'FIN_APPROVE':
                    process_cost_request(tender, task, 'FIN_APPROVE', note)
                elif task.code == 'T06' and act == 'CEO_COST_APPROVE':
                    process_cost_request(tender, task, 'CEO_COST_APPROVE', note)
                return redirect('dashboard')
                
        elif task.code == 'T07':
            tender = Tender.objects.filter(pk=task.payload.get('tender_id')).first()
            if tender and act == 'RECEIPT_UPLOADED':
                if 'receipt' in request.FILES:
                    tender.receipt = request.FILES['receipt']
                    tender.save()
                    register_document(tender, tender.receipt, 'Purchase Receipt', emp, 'FINANCE')
                process_cost_request(tender, task, 'RECEIPT_UPLOADED', note)
                return redirect('dashboard')
                
        elif task.code == 'T08':
            tender = Tender.objects.filter(pk=task.payload.get('tender_id')).first()
            if tender and act == 'complete':
                if 'purchased_bid_document' in request.FILES:
                    tender.purchased_bid_document = request.FILES['purchased_bid_document']
                    tender.save()
                    register_document(tender, tender.purchased_bid_document, 'Purchased Bid Document', emp, 'TENDER')
                task.status = 'COMPLETED'
                task.completed_at = timezone.now()
                task.save()
                return redirect('dashboard')

        else:
            # Standard tasks
            if act == 'start':
                task.status = 'IN_PROGRESS'
            elif act == 'complete':
                task.status = 'COMPLETED'
                task.completed_at = timezone.now()
            elif act == 'reject':
                task.status = 'REJECTED'
                task.completed_at = timezone.now()
            
            payload = dict(task.payload or {})
            if note:
                payload['last_note'] = note
            payload['last_action'] = act
            task.payload = payload
            task.save()
            log_action(emp, 'TASK_%s' % act.upper(), 'Task #%s | note: %s' % (task.id, note), 'Task', task.id, task.company)
            
    return redirect('dashboard')

@login_required
def notifications(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if emp and company:
        # Only show notifications for tasks in the current company, or general memos
        items = Notification.objects.filter(employee=emp).filter(Q(task__company=company) | Q(task__isnull=True))[:100]
    else:
        items = []
    return render(request, 'notifications.html', {'items': items})

@login_required
def mark_all_read(request):
    emp = getattr(request.user, 'employee', None)
    if emp:
        Notification.objects.filter(employee=emp, is_read=False).update(is_read=True)
    return redirect(request.META.get('HTTP_REFERER', '/dashboard/'))

@login_required
def add_personal_task(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if emp and company and request.method == 'POST':
        title = request.POST.get('title')
        due_date = request.POST.get('due_date')
        priority = request.POST.get('priority', 'NORMAL')
        if title:
            Task.objects.create(
                code='MY-TODO', title=title, kind='PERSONAL',
                company=company, assigned_to=emp, assigned_by=emp,
                due_date=due_date if due_date else None,
                priority=priority
            )
    
    # Redirect back to where the user came from
    next_url = request.POST.get('next', 'dashboard')
    return redirect(next_url)

@login_required
def my_todos(request):
    emp = getattr(request.user, 'employee', None)
    if not emp: return redirect('dashboard')
    
    # Get all personal tasks for this employee
    company = getattr(request, 'company', None)
    todos = Task.objects.filter(assigned_to=emp, company=company, kind='PERSONAL').order_by('status', 'due_date', '-priority')
    
    active_todos = todos.exclude(status='COMPLETED')
    completed_todos = todos.filter(status='COMPLETED')[:10] # Only show last 10 completed
    
    return render(request, 'my_todos.html', {
        'active_todos': active_todos,
        'completed_todos': completed_todos,
        'today': timezone.now().date()
    })

@login_required
def my_assigned_tasks(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company: return redirect('dashboard')
    
    # Only show tasks assigned BY management to THIS employee
    tasks = Task.objects.filter(
        assigned_to=emp, company=company, kind='ASSIGNED'
    ).order_by('-created_at')
    
    from django.utils import timezone
    return render(request, 'my_assigned_tasks.html', {
        'tasks': tasks,
        'today': timezone.now().date()
    })
