from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import CompanyResource
from .services import log_action

MAX_QUOTA = 500 * 1024 * 1024  # 500 MB in bytes

@login_required
def resource_list(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')
    
    category_filter = request.GET.get('cat', '')
    resources = CompanyResource.objects.filter(company=company)
    if category_filter:
        resources = resources.filter(category=category_filter)
        
    # Calculate user's current storage usage
    emp = getattr(request.user, 'employee', None)
    user_resources = CompanyResource.objects.filter(company=company, uploaded_by=emp)
    user_usage = sum(r.file_size for r in user_resources)
    user_usage_mb = round(user_usage / (1024 * 1024), 2)
    usage_percent = min(round((user_usage / MAX_QUOTA) * 100), 100)

    return render(request, 'resource_list.html', {
        'resources': resources,
        'category_filter': category_filter,
        'user_usage_mb': user_usage_mb,
        'usage_percent': usage_percent
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
            # Check Quota
            user_resources = CompanyResource.objects.filter(company=company, uploaded_by=emp)
            current_usage = sum(r.file_size for r in user_resources)
            new_file_size = file_obj.size
            
            if current_usage + new_file_size > MAX_QUOTA:
                messages.error(request, f'Upload Failed! Your 500MB quota is full. You are using {round(current_usage/(1024*1024),2)}MB. Please delete old files to free up space.')
                return redirect('resource_list')

            # Save file
            res = CompanyResource.objects.create(
                company=company, title=title, category=cat, file=file_obj, uploaded_by=emp,
                file_size=new_file_size
            )
            log_action(emp, 'UPLOAD_RESOURCE', f'Uploaded {title} to Library', 'Resource', res.id, company)
            messages.success(request, 'File uploaded successfully!')
            
        return redirect('resource_list')

    return render(request, 'resource_form.html')

@login_required
def resource_delete(request, pk):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    res = get_object_or_404(CompanyResource, pk=pk, company=company)
    
    if request.method == 'POST':
        # Only allow the uploader or Admin to delete
        if emp == res.uploaded_by or emp.role in ('ADMIN', 'CEO'):
            # Delete from Cloudflare R2
            res.file.delete(save=False)
            res.delete()
            log_action(emp, 'DELETE_RESOURCE', f'Deleted {res.title} from Library', 'Resource', pk, company)
            messages.success(request, 'File deleted successfully. Quota freed.')
        else:
            messages.error(request, 'You do not have permission to delete this file.')
            
    return redirect('resource_list')
