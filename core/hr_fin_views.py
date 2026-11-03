from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.urls import reverse
from .models import Employee, LeaveRequest, ExpenseRequest, Task, Company, ROLE_CHOICES
from .chain import create_task, find_role
from dateutil import parser as date_parser
from decimal import Decimal


@login_required
def register_employee(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    
    # Only HR, Admin, CEO, GM can register new employees
    if not emp or not company or emp.role not in ('ADMIN', 'CEO', 'GM', 'HR'):
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        password = request.POST.get('password', '')
        role = request.POST.get('role', 'STAFF')
        phone = request.POST.get('phone', '')
        telegram = request.POST.get('telegram', '')
        whatsapp = request.POST.get('whatsapp', '')

        if User.objects.filter(username=username).exists():
            return render(request, 'register_employee.html', {'error': 'Username already exists!', 'roles': ROLE_CHOICES})

        # Create User
        user = User.objects.create_user(username=username, password=password, first_name=first_name, last_name=last_name)
        
        # Create Employee profile
        Employee.objects.create(
            user=user, company=company, role=role, phone=phone,
            telegram_chat_id=telegram, whatsapp_number=whatsapp,
            is_group_level=True # Since all employees work for both companies
        )
        
        from .services import log_action
        log_action(emp, 'REGISTER_EMPLOYEE', f'Created user {username} with role {role}', 'Employee', user.id, company)
        
        return render(request, 'register_employee.html', {'success': f'Successfully registered {first_name} {last_name} ({username})!', 'roles': ROLE_CHOICES})

    return render(request, 'register_employee.html', {'roles': ROLE_CHOICES})

@login_required
def request_leave(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company: return redirect('dashboard')

    if request.method == 'POST':
        start = date_parser.parse(request.POST.get('start_date')).date()
        end = date_parser.parse(request.POST.get('end_date')).date()
        reason = request.POST.get('reason')
        ltype = request.POST.get('leave_type')
        
        leave = LeaveRequest.objects.create(
            employee=emp, company=company, leave_type=ltype,
            start_date=start, end_date=end, reason=reason
        )
        
        # Create Task for HR
        task = Task.objects.create(
            code='HR_LEAVE', title=f'Approve Leave: {emp.full_name}',
            description=f'{ltype} Leave from {start} to {end}. Reason: {reason}',
            kind='ROUTINE', company=company, assigned_to=find_role(company, 'HR'),
            payload={'leave_id': leave.id}
        )
        from .services import notify
        notify(task.assigned_to, 'New Leave Request', task.title, task)
        return redirect('dashboard')

    return render(request, 'request_leave.html')

@login_required
def request_expense(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company: return redirect('dashboard')

    if request.method == 'POST':
        amount = Decimal(request.POST.get('amount', '0'))
        reason = request.POST.get('reason')
        
        exp = ExpenseRequest.objects.create(
            employee=emp, company=company, amount=amount, reason=reason
        )
        
        # Create Task for Finance
        task = Task.objects.create(
            code='FIN_EXPENSE', title=f'Approve Expense: {emp.full_name}',
            description=f'Amount: {amount} ETB. Reason: {reason}',
            kind='ROUTINE', company=company, assigned_to=find_role(company, 'FIN'),
            payload={'expense_id': exp.id}
        )
        from .services import notify
        notify(task.assigned_to, 'New Expense Request', task.title, task)
        return redirect('dashboard')

    return render(request, 'request_expense.html')

@login_required
def staff_directory(request):
    company = getattr(request, 'company', None)
    query = request.GET.get('q', '')
    employees = Employee.objects.all() # All employees work for both companies
    
    if query:
        from django.db.models import Q
        employees = employees.filter(Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query) | Q(role__icontains=query))
        
    return render(request, 'staff_directory.html', {'employees': employees, 'query': query})

import traceback
from django.http import HttpResponse

@login_required
def employee_profile(request, pk):
    try:
        from .models import EmployeeDocument, PerformanceReview
        company = getattr(request, 'company', None)
        emp = get_object_or_404(Employee, pk=pk)
        reviewer = getattr(request.user, 'employee', None)
        
        # Allow HR/Admin/CEO/GM OR the user viewing their own profile
        if not reviewer or (reviewer.role not in ('ADMIN', 'CEO', 'GM', 'HR') and reviewer.pk != emp.pk):
            return redirect('dashboard')

        if request.method == 'POST':
            if 'update_telegram' in request.POST:
                tg_input = request.POST.get('telegram_id', '').strip()
                emp.telegram_id = tg_input
                emp.save()
                return redirect('employee_profile', pk=emp.pk)
            elif 'doc_type' in request.POST and company:
                doc_type = request.POST.get('doc_type')
                title = request.POST.get('title')
                file_obj = request.FILES.get('file')
                if file_obj:
                    from .models import Document
                    reg_doc = Document.objects.create(
                        company=company, title=f"HR: {emp.full_name} - {title}", 
                        file=file_obj, uploaded_by=reviewer, category='HR'
                    )
                    EmployeeDocument.objects.create(
                        employee=emp, doc_type=doc_type, title=title, file=file_obj, registry_doc=reg_doc
                    )
            elif 'rating' in request.POST and reviewer.role in ('ADMIN', 'CEO', 'GM', 'HR'):
                rating = request.POST.get('rating')
                feedback = request.POST.get('feedback')
                PerformanceReview.objects.create(employee=emp, reviewer=reviewer, rating=rating, feedback=feedback)
                
            return redirect('employee_profile', pk=emp.pk)

        return render(request, 'employee_profile.html', {'target_emp': emp, 'company': company})
    
    except Exception as e:
        # THIS WILL SHOW US THE EXACT ERROR ON SCREEN
        error_msg = traceback.format_exc()
        html = f'''
        <div style="padding:20px; font-family:monospace; background:#111; color:#fff; min-height:100vh;">
            <h1 style="color:#ff5555;">Django Error Revealed (DEBUG=False Workaround)</h1>
            <h2 style="color:#ffb86c;">{e}</h2>
            <pre style="background:#222; padding:15px; border-radius:8px; overflow-x:auto; color:#f8f8f2;">{error_msg}</pre>
            <br>
            <a href="/dashboard/" style="color:#4cc7e8; text-decoration:none;">← Go Back to Dashboard</a>
        </div>
        '''
        return HttpResponse(html)

@login_required
def generate_payroll(request):
    from .models import PayrollRecord
    company = getattr(request, 'company', None)
    emp = getattr(request.user, 'employee', None)
    
    if not company or not emp or emp.role not in ('HR', 'ADMIN'):
        return redirect('dashboard')

    if request.method == 'POST':
        month = request.POST.get('month')
        year = request.POST.get('year')
        
        # Calculate total
        total = Employee.objects.filter(is_active=True).aggregate(Sum('base_salary'))['base_salary__sum'] or 0
        
        if total > 0:
            payroll = PayrollRecord.objects.create(
                company=company, month=month, year=year, total_amount=total
            )
            # Create task for Finance
            create_task(company, 'FIN_PAYROLL', 'FIN', 
                        f'Execute Payroll: {month} {year}', 
                        f'Total Payroll Amount: {total} ETB. Please process salaries.', 
                        {'payroll_id': payroll.id, 'amount': str(total)}, 
                        reverse('dashboard'))
            return redirect('payroll_history')
            
    return render(request, 'generate_payroll.html')

@login_required
def payroll_history(request):
    from .models import PayrollRecord
    company = getattr(request, 'company', None)
    payrolls = PayrollRecord.objects.filter(company=company).order_by('-created_at') if company else []
    return render(request, 'payroll_history.html', {'payrolls': payrolls})
