from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Count
from .models import Company, Employee, Tender, Document, Task, Notification
from .services import log_action

@login_required
def saas_dashboard(request):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role != 'SUPERADMIN':
        return redirect('dashboard')
    if 'company_id' in request.session:
        del request.session['company_id']

    companies = Company.objects.all()
    total_companies = companies.count()
    total_employees = Employee.objects.count()
    total_tenders = Tender.objects.count()
    
    total_storage = sum(d.file.size for d in Document.objects.all() if d.file)
    total_storage_mb = round(total_storage / (1024 * 1024), 2)

    company_stats = []
    for c in companies:
        company_stats.append({
            'company': c,
            'employees': Employee.objects.filter(company=c).count(),
            'tenders': Tender.objects.filter(company=c).count(),
        })

    return render(request, 'saas_dashboard.html', {
        'total_companies': total_companies,
        'total_employees': total_employees,
        'total_tenders': total_tenders,
        'total_storage_mb': total_storage_mb,
        'company_stats': company_stats
    })

@login_required
def saas_add_company(request):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role != 'SUPERADMIN':
        return redirect('dashboard')

    if request.method == 'POST':
        name = request.POST.get('name')
        short_name = request.POST.get('short_name')
        
        if Company.objects.filter(short_name=short_name).exists():
            messages.error(request, 'Company code already exists!')
        else:
            Company.objects.create(name=name, short_name=short_name)
            messages.success(request, f'Company {name} successfully onboarded!')
            return redirect('saas_dashboard')

    return render(request, 'saas_add_company.html')

@login_required
def saas_delete_company(request, pk):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role != 'SUPERADMIN':
        return redirect('dashboard')

    company = get_object_or_404(Company, pk=pk)
    if request.method == 'POST':
        # Delete all users belonging to this company
        User.objects.filter(employee__company=company).delete()
        company_name = company.name
        company.delete()
        messages.success(request, f'Company {company_name} and all its data were deleted.')
        
    return redirect('saas_dashboard')

@login_required
def saas_manage_employees(request):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role != 'SUPERADMIN':
        return redirect('dashboard')

    companies = Company.objects.all()
    employees = Employee.objects.all().order_by('company', 'role')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        role = request.POST.get('role', 'STAFF')
        company_id = request.POST.get('company_id')
        
        company = Company.objects.filter(id=company_id).first()

        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists!')
        elif not company:
            messages.error(request, 'Invalid company selected.')
        else:
            user = User.objects.create_user(username=username, password=password, first_name=first_name, last_name=last_name)
            Employee.objects.create(user=user, company=company, role=role, is_group_level=True, is_active=True)
            messages.success(request, f'Employee {first_name} {last_name} created successfully.')
            return redirect('saas_manage_employees')

    from core.models import ROLE_CHOICES
    return render(request, 'saas_employees.html', {
        'companies': companies, 'employees': employees, 'roles': ROLE_CHOICES
    })

@login_required
def saas_clear_test_data(request):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role != 'SUPERADMIN':
        return redirect('dashboard')

    if request.method == 'POST':
        # Clear transactional data but keep companies and users
        Task.objects.all().delete()
        Tender.objects.all().delete()
        Document.objects.all().delete()
        Notification.objects.all().delete()
        
        messages.success(request, 'All test Tenders, Tasks, Documents, and Notifications have been permanently deleted.')
        return redirect('saas_dashboard')

    return render(request, 'saas_clear_data.html')
