from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db.models import Q
from .models import Memo, Employee, Task, Company, ROLE_CHOICES
from .services import log_action

@login_required
def memo_list(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company:
        return redirect('select_company')

    memos = Memo.objects.filter(company=company).filter(
        Q(recipient_employee=emp) | Q(recipient_role=emp.role) | Q(recipient_role='', recipient_employee=None)
    )
    return render(request, 'memo_list.html', {'memos': memos})

@login_required
def memo_create(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company or emp.role not in ('ADMIN', 'CEO', 'GM', 'VGM'):
        return redirect('dashboard')

    if request.method == 'POST':
        title = request.POST.get('title')
        content = request.POST.get('content')
        rec_role = request.POST.get('recipient_role', '')
        rec_emp_id = request.POST.get('recipient_employee', '')
        
        rec_emp = None
        if rec_emp_id:
            rec_emp = Employee.objects.filter(id=rec_emp_id).first()

        Memo.objects.create(company=company, title=title, content=content, sender=emp, recipient_role=rec_role, recipient_employee=rec_emp)
        log_action(emp, 'MEMO_CREATED', title, 'Memo', '', company)
        return redirect('memo_list')

    employees = Employee.objects.all()
    roles = ROLE_CHOICES
    return render(request, 'memo_form.html', {'employees': employees, 'roles': roles})

@login_required
def memo_read(request, pk):
    emp = getattr(request.user, 'employee', None)
    memo = Memo.objects.filter(pk=pk).first()
    if memo and emp:
        memo.is_read = True
        memo.save()
    return redirect('memo_list')

from dateutil import parser as date_parser
from .services import notify

@login_required
def assign_task(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    
    # Expanded roles that can assign tasks
    if not emp or not company or emp.role not in ('ADMIN', 'CEO', 'GM', 'VGM', 'BIDM', 'FIN', 'HR', 'PROC', 'LOG', 'LEGAL'):
        return redirect('dashboard')

    if request.method == 'POST':
        title = request.POST.get('title')
        desc = request.POST.get('description', '')
        assignee_id = request.POST.get('assignee')
        due_date_str = request.POST.get('due_date')
        priority = request.POST.get('priority', 'NORMAL')
        
        assignee = Employee.objects.filter(id=assignee_id).first()
        due_date = date_parser.parse(due_date_str).date() if due_date_str else None
        
        if assignee:
            task = Task.objects.create(
                code='ASSIGNED', title=title, description=desc, kind='ASSIGNED',
                company=company, assigned_to=assignee, assigned_by=emp,
                due_date=due_date, priority=priority
            )
            # Send notification to the assignee
            notify(assignee, f'New Task from {emp.full_name}: {title}', desc, task, '/dashboard/')
            return redirect('delegated_tasks')

    employees = Employee.objects.all().order_by('role')
    pre_role = request.GET.get('role', '')
    return render(request, 'assign_task.html', {'employees': employees, 'pre_role': pre_role})


@login_required
def delegated_tasks(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    
    # Ensure only management/department heads can access this
    if not emp or not company or emp.role not in ('ADMIN', 'CEO', 'GM', 'VGM', 'BIDM', 'FIN', 'HR', 'PROC', 'LOG', 'LEGAL'):
        return redirect('dashboard')
        
    # Only show tasks assigned by this specific manager
    tasks = Task.objects.filter(assigned_by=emp, company=company).order_by('-created_at')
    
    from django.utils import timezone
    return render(request, 'delegated_tasks.html', {
        'tasks': tasks,
        'today': timezone.now().date()
    })
