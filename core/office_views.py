from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from .models import CourierDispatch, SupplyRequest, VisitorLog, Document, Task
from .chain import create_task, find_role

@login_required
def office_dashboard(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')

    dispatches = CourierDispatch.objects.filter(company=company).order_by('-dispatch_date')[:10]
    visitors = VisitorLog.objects.filter(company=company, check_out_time__isnull=True).order_by('-check_in_time')
    
    if request.method == 'POST':
        if 'dispatch' in request.POST:
            CourierDispatch.objects.create(
                company=company, courier_name=request.POST.get('courier_name'),
                recipient=request.POST.get('recipient'), logged_by=request.user.employee
            )
        elif 'visitor' in request.POST:
            VisitorLog.objects.create(
                company=company, visitor_name=request.POST.get('visitor_name'),
                visiting_employee_id=request.POST.get('visiting_employee'), purpose=request.POST.get('purpose')
            )
        return redirect('office_dashboard')

    from .models import Employee
    employees = Employee.objects.all()
    return render(request, 'office_dashboard.html', {'dispatches': dispatches, 'visitors': visitors, 'employees': employees})

@login_required
def document_checkout(request, pk):
    company = getattr(request, 'company', None)
    doc = get_object_or_404(Document, pk=pk, company=company)
    
    if request.method == 'POST':
        emp_id = request.POST.get('employee_id')
        if emp_id:
            from .models import Employee
            doc.checked_out_to = Employee.objects.get(pk=emp_id)
            doc.checkout_date = timezone.now().date()
        else:
            doc.checked_out_to = None
            doc.checkout_date = None
        doc.save()
        return redirect('document_list')

    from .models import Employee
    employees = Employee.objects.all()
    return render(request, 'doc_checkout.html', {'doc': doc, 'employees': employees})

@login_required
def supply_requests(request):
    company = getattr(request, 'company', None)
    emp = getattr(request.user, 'employee', None)
    
    if request.method == 'POST' and 'item_name' in request.POST:
        SupplyRequest.objects.create(
            company=company, employee=emp, item_name=request.POST.get('item_name'), quantity=request.POST.get('quantity', 1)
        )
        # Create task for Office Assistant
        create_task(company, 'SUPPLY_REQ', 'OFFICE', f'Supply Request: {request.POST.get("item_name")}', f'Requested by {emp.full_name}', {}, '')
        return redirect('supply_requests')

    if request.method == 'POST' and 'approve_id' in request.POST:
        req = SupplyRequest.objects.get(pk=request.POST.get('approve_id'))
        req.status = 'FULFILLED'
        req.save()
        return redirect('supply_requests')

    reqs = SupplyRequest.objects.filter(company=company).order_by('-created_at')
    return render(request, 'supply_requests.html', {'reqs': reqs})


@login_required
def office_manual(request):
    return render(request, 'office_manual.html')
