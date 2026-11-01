from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from .models import CompanyResource

@login_required
def resource_list(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')
    
    category_filter = request.GET.get('cat', '')
    resources = CompanyResource.objects.filter(company=company)
    if category_filter:
        resources = resources.filter(category=category_filter)
        
    return render(request, 'resource_list.html', {
        'resources': resources,
        'category_filter': category_filter
    })

@login_required
def resource_upload(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company: return redirect('select_company')

    if request.method == 'POST':
        title = request.POST.get('title')
        cat = request.POST.get('category')
        file_obj = request.FILES.get('file')
        
        if file_obj:
            CompanyResource.objects.create(
                company=company, title=title, category=cat, file=file_obj, uploaded_by=emp
            )
        return redirect('resource_list')

    return render(request, 'resource_form.html')
