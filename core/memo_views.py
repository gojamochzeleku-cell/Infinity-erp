from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render, redirect

from .models import (
    Memo, Employee, Task, MEMBERSHIP_ROLE_CHOICES
)
from .services import log_action, notify


MEMO_CREATE_ROLES = {'ADMIN', 'CEO', 'GM', 'VGM'}

TASK_ASSIGN_ROLES = {
    'ADMIN', 'CEO', 'GM', 'VGM', 'BIDM',
    'FIN', 'HR', 'PROC', 'LOG', 'LEGAL'
}


def _company_context(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if (
        not emp or not company or not membership
        or not emp.is_active
        or not membership.is_active
        or membership.employee_id != emp.id
        or membership.company_id != company.id
        or not company.is_active
    ):
        return None, None, set()

    roles = set(
        membership.roles.values_list('role', flat=True)
    )
    return emp, company, roles


def _company_employees(company):
    return Employee.objects.filter(
        is_active=True,
        user__is_active=True,
        company_memberships__company=company,
        company_memberships__is_active=True,
        company_memberships__company__is_active=True,
    ).distinct().order_by('user__username')


@login_required
def memo_list(request):
    emp, company, roles = _company_context(request)

    if not emp:
        return redirect('select_company')

    visibility = (
        Q(recipient_employee=emp)
        | Q(recipient_employee=None, recipient_role='')
    )

    if roles:
        visibility |= Q(
            recipient_employee=None,
            recipient_role__in=roles
        )

    memos = Memo.objects.filter(
        company=company
    ).filter(visibility).select_related(
        'sender', 'recipient_employee'
    ).order_by('-created_at')

    return render(request, 'memo_list.html', {
        'memos': memos,
        'membership_roles': roles,
    })


@login_required
def memo_create(request):
    emp, company, roles = _company_context(request)

    if not emp:
        return redirect('select_company')

    if not roles.intersection(MEMO_CREATE_ROLES):
        messages.error(request, 'You are not authorized to create memos.')
        return redirect('memo_list')

    employees = _company_employees(company)
    role_choices = MEMBERSHIP_ROLE_CHOICES

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()
        rec_role = request.POST.get('recipient_role', '').strip()
        rec_emp_id = request.POST.get('recipient_employee', '').strip()

        valid_roles = {code for code, label in role_choices}

        if not title or not content:
            messages.error(request, 'Title and content are required.')
        elif rec_role and rec_role not in valid_roles:
            messages.error(request, 'Invalid recipient role.')
        elif rec_role and rec_emp_id:
            messages.error(
                request,
                'Choose either a role or one employee, not both.'
            )
        else:
            rec_emp = None

            if rec_emp_id:
                rec_emp = employees.filter(pk=rec_emp_id).first()

                if not rec_emp:
                    messages.error(
                        request,
                        'The selected employee is not an active member of this company.'
                    )

            if not rec_emp_id or rec_emp:
                Memo.objects.create(
                    company=company,
                    title=title,
                    content=content,
                    sender=emp,
                    recipient_role=rec_role,
                    recipient_employee=rec_emp,
                )
                log_action(
                    emp, 'MEMO_CREATED', title, 'Memo', '', company
                )
                messages.success(request, 'Memo sent successfully.')
                return redirect('memo_list')

    return render(request, 'memo_form.html', {
        'employees': employees,
        'roles': role_choices,
    })


@login_required
def memo_read(request, pk):
    emp, company, roles = _company_context(request)

    if not emp:
        return redirect('select_company')

    # The existing is_read field is shared by all recipients.
    # Only a direct employee memo can safely update it.
    if request.method == 'POST':
        Memo.objects.filter(
            pk=pk,
            company=company,
            recipient_employee=emp,
        ).update(is_read=True)

    return redirect('memo_list')


@login_required
def assign_task(request):
    emp, company, roles = _company_context(request)

    if not emp:
        return redirect('select_company')

    if not roles.intersection(TASK_ASSIGN_ROLES):
        messages.error(request, 'You are not authorized to assign tasks.')
        return redirect('dashboard')

    employees = _company_employees(company)
    pre_role = request.GET.get('role', '').strip()

    valid_roles = {code for code, label in MEMBERSHIP_ROLE_CHOICES}

    if pre_role in valid_roles:
        employees = employees.filter(
            company_memberships__roles__role=pre_role
        ).distinct()

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        desc = request.POST.get('description', '').strip()
        assignee_id = request.POST.get('assignee', '').strip()
        due_date_str = request.POST.get('due_date', '').strip()
        priority = request.POST.get('priority', 'NORMAL').strip()

        due_date = None
        valid = True

        if not title:
            messages.error(request, 'Task title is required.')
            valid = False

        if priority not in {'NORMAL', 'HIGH', 'URGENT'}:
            messages.error(request, 'Invalid task priority.')
            valid = False

        if due_date_str:
            try:
                due_date = datetime.strptime(
                    due_date_str, '%Y-%m-%d'
                ).date()
            except ValueError:
                messages.error(request, 'Invalid due date.')
                valid = False

        assignee = employees.filter(pk=assignee_id).first()

        if not assignee:
            messages.error(
                request,
                'Select an active employee belonging to this company.'
            )
            valid = False

        if valid:
            task = Task.objects.create(
                code='ASSIGNED',
                title=title,
                description=desc,
                kind='ASSIGNED',
                company=company,
                assigned_to=assignee,
                assigned_by=emp,
                due_date=due_date,
                priority=priority,
            )

            notify(
                assignee,
                f'New Task from {emp.full_name}: {title}',
                desc,
                task,
                '/dashboard/',
            )

            messages.success(request, 'Task assigned successfully.')
            return redirect('delegated_tasks')

    return render(request, 'assign_task.html', {
        'employees': employees,
        'pre_role': pre_role,
    })


@login_required
def delegated_tasks(request):
    emp, company, roles = _company_context(request)

    if not emp:
        return redirect('select_company')

    if not roles.intersection(TASK_ASSIGN_ROLES):
        return redirect('dashboard')

    tasks = Task.objects.filter(
        assigned_by=emp,
        company=company,
    ).select_related('assigned_to').order_by('-created_at')

    from django.utils import timezone

    return render(request, 'delegated_tasks.html', {
        'tasks': tasks,
        'today': timezone.now().date(),
    })
