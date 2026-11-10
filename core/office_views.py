from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from .models import (
    CourierDispatch, SupplyRequest, VisitorLog, Document, Employee
)
from .chain import create_task


def _office_context(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or not emp
        or not emp.is_active
        or not company
        or not membership
        or not membership.is_active
        or membership.employee_id != emp.id
        or membership.company_id != company.id
    ):
        return None, None, set()

    roles = set(membership.roles.values_list('role', flat=True))
    return emp, company, roles


def _company_employees(company):
    return Employee.objects.filter(
        is_active=True,
        company_memberships__company=company,
        company_memberships__is_active=True,
    ).distinct()


@login_required
def office_dashboard(request):
    emp, company, roles = _office_context(request)
    if not company:
        return redirect('select_company')
    if not roles.intersection({'OFFICE', 'ADMIN'}):
        return redirect('dashboard')

    dispatches = CourierDispatch.objects.filter(
        company=company
    ).order_by('-dispatch_date')[:10]

    visitors = VisitorLog.objects.filter(
        company=company,
        check_out_time__isnull=True
    ).order_by('-check_in_time')

    employees = _company_employees(company)

    if request.method == 'POST':
        if 'dispatch' in request.POST:
            CourierDispatch.objects.create(
                company=company,
                courier_name=request.POST.get('courier_name', '').strip(),
                recipient=request.POST.get('recipient', '').strip(),
                logged_by=emp,
            )
        elif 'visitor' in request.POST:
            visitor_employee = employees.filter(
                pk=request.POST.get('visiting_employee')
            ).first()
            if not visitor_employee:
                messages.error(request, 'Select an active employee in this company.')
                return redirect('office_dashboard')

            VisitorLog.objects.create(
                company=company,
                visitor_name=request.POST.get('visitor_name', '').strip(),
                visitor_company=request.POST.get('visitor_company', '').strip(),
                visiting_employee=visitor_employee,
                purpose=request.POST.get('purpose', '').strip(),
            )
        return redirect('office_dashboard')

    return render(request, 'office_dashboard.html', {
        'dispatches': dispatches,
        'visitors': visitors,
        'employees': employees,
    })


@login_required
def document_checkout(request, pk):
    emp, company, roles = _office_context(request)
    if not company:
        return redirect('select_company')
    if not roles.intersection({'OFFICE', 'ADMIN'}):
        return redirect('dashboard')

    doc = get_object_or_404(Document, pk=pk, company=company)
    employees = _company_employees(company)

    if request.method == 'POST':
        emp_id = request.POST.get('employee_id')
        if emp_id:
            selected_employee = employees.filter(pk=emp_id).first()
            if not selected_employee:
                messages.error(request, 'Select an active employee in this company.')
                return redirect('document_checkout', pk=doc.pk)
            doc.checked_out_to = selected_employee
            doc.checkout_date = timezone.now().date()
        else:
            doc.checked_out_to = None
            doc.checkout_date = None

        doc.save()
        return redirect('document_list')

    return render(request, 'doc_checkout.html', {
        'doc': doc,
        'employees': employees,
    })


@login_required
def supply_requests(request):
    emp, company, roles = _office_context(request)
    if not company:
        return redirect('select_company')

    if request.method == 'POST':
        if 'item_name' in request.POST:
            item_name = request.POST.get('item_name', '').strip()
            try:
                quantity = int(request.POST.get('quantity', 1))
            except (TypeError, ValueError):
                quantity = 0

            if not item_name or quantity < 1:
                messages.error(request, 'Enter a valid item and quantity.')
                return redirect('supply_requests')

            SupplyRequest.objects.create(
                company=company,
                employee=emp,
                item_name=item_name,
                quantity=quantity,
            )
            create_task(
                company,
                'SUPPLY_REQ',
                'OFFICE',
                f'Supply Request: {item_name}',
                f'Requested by {emp.full_name}',
                {},
                '',
            )
            return redirect('supply_requests')

        if 'approve_id' in request.POST:
            if not roles.intersection({'OFFICE', 'ADMIN'}):
                return redirect('dashboard')

            req = get_object_or_404(
                SupplyRequest,
                pk=request.POST.get('approve_id'),
                company=company,
            )
            if req.status != 'FULFILLED':
                req.status = 'FULFILLED'
                req.save(update_fields=['status'])
            return redirect('supply_requests')

    reqs = SupplyRequest.objects.filter(
        company=company
    ).select_related('employee').order_by('-created_at')

    return render(request, 'supply_requests.html', {
        'reqs': reqs,
        'membership_roles': roles,
    })


@login_required
def office_manual(request):
    emp, company, roles = _office_context(request)
    if not company:
        return redirect('select_company')
    return render(request, 'office_manual.html')
