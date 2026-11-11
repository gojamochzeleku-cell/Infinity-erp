from django.urls import reverse
import threading
from django.db import connection
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.db.models import Q
from decimal import Decimal

from .models import Company, Task, Notification, Tender, LeaveRequest, ExpenseRequest, CompanyMembership, MembershipRole
from .services import log_action, log_task_history
from .chain import (
    register_document,
    advance_tender_chain,
    process_cost_request,
    trigger_ai_tasks,
    advance_tender_execution_chain,
    find_role,
)

class InfinityLoginView(LoginView):
    template_name = 'login.html'
    redirect_authenticated_user = True
    
    def form_valid(self, form):
        # Log the user in
        response = super().form_valid(form)
        # Ensure the company session is completely clear on a fresh login
        if 'company_id' in self.request.session:
            del self.request.session['company_id']
        return response
        
    def get_success_url(self):
        # Every user enters through company membership selection.
        return str(reverse('select_company'))

def home(request):
    if request.user.is_authenticated:
        return redirect('select_company')
    return redirect('login')

@login_required
def select_company(request):
    memberships = CompanyMembership.objects.filter(
        employee__user=request.user,
        employee__is_active=True,
        is_active=True,
        company__is_active=True,
    ).select_related('company').order_by('company__name')

    companies = Company.objects.filter(
        pk__in=memberships.values('company_id'),
        is_active=True,
    ).order_by('name')

    if request.method == 'POST':
        raw_company_id = request.POST.get('company_id', '')

        try:
            company_id = int(raw_company_id)
        except (TypeError, ValueError):
            company_id = None

        membership = None
        if company_id is not None:
            membership = memberships.filter(
                company_id=company_id
            ).first()

        if membership is not None:
            request.session['company_id'] = membership.company_id
            return redirect('dashboard')

    return render(
        request,
        'select_company.html',
        {'companies': companies},
    )

@login_required
def dashboard(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if emp is None:
        return render(request, 'no_profile.html')

    if (
        company is None
        or membership is None
        or membership.employee_id != emp.id
        or membership.company_id != company.id
        or not membership.is_active
    ):
        return redirect('select_company')

    # Tasks are visible only to their actual designated assignee.
    qs = Task.objects.filter(
        company=company,
        assigned_to=emp,
    ).exclude(
        status__in=['COMPLETED', 'CANCELLED']
    )

    tasks = list(qs.order_by('-created_at')[:100])

    done = Task.objects.filter(
        company=company,
        assigned_to=emp,
        status='COMPLETED',
    )

    stats = {
        'pending': sum(1 for t in tasks if t.status == 'PENDING'),
        'in_progress': sum(1 for t in tasks if t.status == 'IN_PROGRESS'),
        'completed': done.count(),
    }

    # Executive classification uses company-specific membership roles.
    roles = set(
        MembershipRole.objects.filter(
            membership=membership
        ).values_list('role', flat=True)
    )

    if roles.intersection({'CEO', 'GM', 'ADMIN'}):
        approval_codes = [
            'T03', 'T06', 'T12', 'T16',
            'I03', 'I08', 'I16',
            'CEO_EXPENSE', 'GM_AGENT_APPROVED', 'GM_LEAVE',
        ]

        executive_approvals = [
            t for t in tasks if t.code in approval_codes
        ]

        active_tenders = Tender.objects.filter(
            company=company
        ).exclude(
            status__in=['WON', 'LOST', 'COMPLETED', 'DRAFT']
        ).order_by('-updated_at')[:5]

        from .models import AuditLog
        recent_activity = AuditLog.objects.filter(
            company=company
        ).order_by('-created_at')[:5]

        return render(request, 'dashboard_executive.html', {
            'approvals': executive_approvals,
            'other_tasks': [
                t for t in tasks if t.code not in approval_codes
            ],
            'stats': stats,
            'active_tenders': active_tenders,
            'recent_activity': recent_activity,
            'today': timezone.now().date(),
        })

    return render(request, 'dashboard.html', {
        'tasks': tasks,
        'stats': stats,
        'today': timezone.now().date(),
    })

@login_required
def task_detail(request, pk):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if (
        emp is None
        or company is None
        or membership is None
        or membership.employee_id != emp.id
        or membership.company_id != company.id
        or not membership.is_active
    ):
        return redirect('select_company')

    task = get_object_or_404(
        Task,
        pk=pk,
        company=company,
        assigned_to=emp,
    )

    tender = None
    tender_id = (task.payload or {}).get('tender_id')

    if tender_id:
        tender = Tender.objects.filter(
            pk=tender_id,
            company=company,
        ).first()

    from .models import Product
    all_products = Product.objects.filter(company=company)

    if request.method == 'POST' and 'comment_text' in request.POST:
        from .models import TaskComment

        comment_text = request.POST.get('comment_text', '').strip()

        if comment_text:
            TaskComment.objects.create(
                task=task,
                author=emp,
                text=comment_text,
            )

            if task.assigned_by and task.assigned_by != emp:
                from .services import notify
                notify(
                    task.assigned_by,
                    f'New Comment on {task.code}',
                    f'{emp.full_name}: {comment_text}',
                    task,
                )

        return redirect('task_detail', pk=task.id)

    return render(request, 'task_detail.html', {
        'task': task,
        'tender': tender,
        'all_products': all_products,
    })

@login_required
def task_action(request, pk):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if (
        emp is None
        or company is None
        or membership is None
        or membership.employee_id != emp.id
        or membership.company_id != company.id
        or not membership.is_active
    ):
        return redirect('select_company')

    # A task must belong to the selected company and be assigned
    # to this exact employee. Legacy global roles are not permissions.
    task = get_object_or_404(
        Task,
        pk=pk,
        company=company,
        assigned_to=emp,
    )

    if request.method != 'POST':
        return redirect('dashboard')

    act = request.POST.get('action', '')
    note = request.POST.get('note', '').strip()

    tender_id = (task.payload or {}).get('tender_id')
    tender = None

    if tender_id:
        tender = Tender.objects.filter(
            pk=tender_id,
            company=company,
        ).first()

    if task.code in ('T02', 'T03'):
        if tender and act in ('GO', 'NO_GO', 'APPROVED', 'REJECTED'):
            result = advance_tender_chain(request, tender, task, act, note)
            if result:
                messages.error(request, result)
            return redirect('dashboard')

    elif task.code == 'T04':
        if tender and act == 'SUBMIT_COST':
            try:
                transport_cost = Decimal(
                    request.POST.get('transport_cost', '0')
                )
            except Exception:
                transport_cost = Decimal('0')

            tender.transport_cost = transport_cost
            tender.transport_from = request.POST.get('transport_from', '')
            tender.transport_to = request.POST.get('transport_to', '')
            tender.transport_reason = request.POST.get('transport_reason', '')
            tender.total_cost = (
                tender.bid_document_cost_amount + tender.transport_cost
            )
            tender.save()

            result = process_cost_request(tender, task, 'SUBMIT_COST', note)
            if result:
                messages.error(request, result)
            return redirect('dashboard')

    elif task.code in ('T05', 'T06'):
        if tender:
            if task.code == 'T05' and act == 'FIN_APPROVE':
                result = process_cost_request(tender, task, 'FIN_APPROVE', note)
                if result:
                    messages.error(request, result)
            elif task.code == 'T06' and act == 'CEO_COST_APPROVE':
                result = process_cost_request(tender, task, 'CEO_COST_APPROVE', note)
                if result:
                    messages.error(request, result)

            return redirect('dashboard')

    elif task.code == 'T07':
        if tender and act == 'RECEIPT_UPLOADED':
            if 'receipt' in request.FILES:
                tender.receipt = request.FILES['receipt']
                tender.save()

                register_document(
                    tender,
                    tender.receipt,
                    'Purchase Receipt',
                    emp,
                    'FINANCE',
                )

            result = process_cost_request(
                tender, task, 'RECEIPT_UPLOADED', note
            )
            if result:
                messages.error(request, result)
            return redirect('dashboard')

    elif task.code == 'T08':
        if tender and act == 'complete':
            next_role = 'BIDOP' if tender.lots.exists() else 'BIDM'
            if find_role(tender.company, next_role) is None:
                messages.error(
                    request,
                    "Cannot continue: no active employee is assigned to the "
                    "%s workflow role. Assign the role in Manage Users first."
                    % next_role,
                )
                return redirect('dashboard')

            if 'purchased_bid_document' in request.FILES:
                tender.purchased_bid_document = request.FILES[
                    'purchased_bid_document'
                ]
                tender.bid_document_uploaded_at = timezone.now()
                tender.bid_document_purged = False
                tender.save()

                register_document(
                    tender,
                    tender.purchased_bid_document,
                    'Purchased Bid Document',
                    emp,
                    'TENDER',
                )

            task.status = 'COMPLETED'
            task.completed_at = timezone.now()
            task.save()

            if tender.lots.exists():
                from .chain import trigger_lot_assemblies
                trigger_lot_assemblies(tender)
            else:
                trigger_ai_tasks(tender)

            return redirect('dashboard')

    elif task.code == 'T09':
        if tender:
            return redirect('war_room', pk=tender.id)

    elif task.code in ('T10', 'T11', 'T12'):
        if tender:
            if task.code == 'T10':
                section = (task.payload or {}).get('section')
                if section in {'summary', 'checklist', 'brands', 'suppliers', 'all'}:
                    current_roles = set(
                        membership.roles.values_list('role', flat=True)
                    )
                    if 'BIDOP' not in current_roles:
                        return redirect('dashboard')
                    return redirect(
                        'bid_operator_workspace',
                        pk=tender.id,
                        task_id=task.id,
                    )

            if act == 'complete':
                if 'task_file' in request.FILES:
                    task.uploaded_file = request.FILES['task_file']

                    register_document(
                        tender,
                        task.uploaded_file,
                        f'Form Upload ({task.code})',
                        emp,
                        'TENDER',
                    )

                task.status = 'COMPLETED'
                task.completed_at = timezone.now()
                task.save()
                return redirect('dashboard')

    elif task.code in (
        'T14', 'T15', 'T16', 'T17', 'T18',
        'T19', 'T20', 'T21', 'T22', 'T23', 'T24',
        'I01', 'I02', 'I03', 'I04', 'I05',
        'I06', 'I06G', 'I07', 'I08', 'I09', 'I10',
        'I11', 'I12', 'I13', 'I14', 'I15', 'I16',
    ):
        if tender:
            result = advance_tender_execution_chain(
                tender,
                task,
                act,
                note,
                request.POST,
                request.FILES,
                emp,
            )
            task.refresh_from_db()
            if result:
                messages.error(request, result)
            elif task.code == 'I11' and task.status == 'IN_PROGRESS':
                messages.warning(
                    request,
                    'Inspection saved, but no usable goods are available. '
                    'I11 remains open; replacement or corrective action is required.',
                )
            else:
                messages.success(
                    request,
                    'Task completed and workflow updated successfully.',
                )
            return redirect('dashboard')

    else:
        if act == 'start':
            task.status = 'IN_PROGRESS'

        elif act == 'complete':
            task.status = 'COMPLETED'
            task.completed_at = timezone.now()
            log_task_history(task, 'COMPLETED', note, emp)
            messages.success(
                request,
                'Task marked as completed.',
            )

        elif act == 'reject':
            task.status = 'REJECTED'
            task.completed_at = timezone.now()
            log_task_history(task, 'REJECTED', note, emp)
            messages.warning(
                request,
                'Task was rejected.',
            )

        else:
            return redirect('dashboard')

        payload = dict(task.payload or {})

        if note:
            payload['last_note'] = note

        payload['last_action'] = act
        task.payload = payload
        task.save()

    return redirect('dashboard')

@login_required
def notifications(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if (
        emp is None
        or company is None
        or membership is None
        or membership.employee_id != emp.id
        or membership.company_id != company.id
        or not membership.is_active
    ):
        return redirect('select_company')

    # Taskless notifications have no reliable company ownership.
    # Exclude them until Notification receives a company field.
    items = Notification.objects.filter(
        employee=emp,
        task__company=company,
    ).select_related('task')[:100]

    return render(
        request,
        'notifications.html',
        {'items': items},
    )


@login_required
def mark_all_read(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if (
        emp is None
        or company is None
        or membership is None
        or membership.employee_id != emp.id
        or membership.company_id != company.id
        or not membership.is_active
    ):
        return redirect('select_company')

    if request.method == 'POST':
        Notification.objects.filter(
            employee=emp,
            task__company=company,
            is_read=False,
        ).update(is_read=True)

    return redirect(
        request.META.get('HTTP_REFERER', '/dashboard/')
    )

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
