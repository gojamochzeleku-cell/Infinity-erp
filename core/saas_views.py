from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Count, Sum
from .models import Company, Employee, Tender, Document
from .services import notify

@login_required
def saas_dashboard(request):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role != 'SUPERADMIN':
        return redirect('dashboard')

    # Gather Global SaaS Metrics
    companies = Company.objects.all()
    total_companies = companies.count()
    total_employees = Employee.objects.count()
    total_tenders = Tender.objects.count()
    
    # Calculate total storage across all tenants
    all_docs = Document.objects.all()
    total_storage = sum(d.file.size for d in all_docs if d.file)
    total_storage_mb = round(total_storage / (1024 * 1024), 2)

    # Company breakdown
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
            messages.success(request, f'Company {name} successfully onboarded to the SaaS platform!')
            return redirect('saas_dashboard')

    return render(request, 'saas_add_company.html')
