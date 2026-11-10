from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404

from .models import CompanyResource, CompanyMembership
from .services import log_action

MAX_QUOTA = 500 * 1024 * 1024


def _resource_context(request):
    user = request.user
    emp = getattr(user, "employee", None)
    company = getattr(request, "company", None)
    if not user.is_authenticated or not user.is_active:
        return None, None, set()
    if not emp or not emp.is_active or not company:
        return None, None, set()
    membership = CompanyMembership.objects.filter(
        employee=emp, company=company, is_active=True
    ).first()
    if not membership:
        return None, None, set()
    roles = set(membership.roles.values_list("role", flat=True))
    return emp, company, roles


@login_required
def resource_list(request):
    emp, company, roles = _resource_context(request)
    if not company:
        return redirect("select_company")

    category_filter = request.GET.get("cat", "").strip()
    valid_categories = {value for value, label in CompanyResource.CATEGORY_CHOICES}
    resources = CompanyResource.objects.filter(
        company=company
    ).select_related("uploaded_by")

    if category_filter in valid_categories:
        resources = resources.filter(category=category_filter)
    elif category_filter:
        category_filter = ""

    user_resources = CompanyResource.objects.filter(
        company=company, uploaded_by=emp
    )
    user_usage = sum(user_resources.values_list("file_size", flat=True))
    user_usage_mb = round(user_usage / (1024 * 1024), 2)
    usage_percent = min(round((user_usage / MAX_QUOTA) * 100), 100)

    return render(request, "resource_list.html", {
        "resources": resources,
        "category_filter": category_filter,
        "user_usage_mb": user_usage_mb,
        "usage_percent": usage_percent,
    })


@login_required
def resource_upload(request):
    emp, company, roles = _resource_context(request)
    if not company:
        return redirect("select_company")

    if request.method == "GET":
        return render(request, "resource_form.html")
    if request.method != "POST":
        return redirect("resource_list")

    title = request.POST.get("title", "").strip()[:200]
    category = request.POST.get("category", "OTHER").strip()
    file_obj = request.FILES.get("file")
    valid_categories = {value for value, label in CompanyResource.CATEGORY_CHOICES}

    if not title or not file_obj or category not in valid_categories:
        messages.error(request, "Enter a title, choose a valid category, and select a file.")
        return redirect("resource_upload")

    user_resources = CompanyResource.objects.filter(
        company=company, uploaded_by=emp
    )
    current_usage = sum(user_resources.values_list("file_size", flat=True))
    new_file_size = file_obj.size

    if current_usage + new_file_size > MAX_QUOTA:
        messages.error(
            request,
            f"Upload failed: your 500 MB quota is full. You are using {round(current_usage / (1024 * 1024), 2)} MB.",
        )
        return redirect("resource_list")

    resource = CompanyResource.objects.create(
        company=company,
        title=title,
        category=category,
        file=file_obj,
        uploaded_by=emp,
        file_size=new_file_size,
    )
    log_action(
        emp, "UPLOAD_RESOURCE", f"Uploaded {title} to Library",
        "Resource", resource.id, company
    )
    messages.success(request, "File uploaded successfully!")
    return redirect("resource_list")


@login_required
def resource_delete(request, pk):
    emp, company, roles = _resource_context(request)
    if not company:
        return redirect("select_company")
    if request.method != "POST":
        return redirect("resource_list")

    resource = get_object_or_404(CompanyResource, pk=pk, company=company)
    if emp != resource.uploaded_by and not roles.intersection({"ADMIN", "CEO"}):
        messages.error(request, "You do not have permission to delete this file.")
        return redirect("resource_list")

    title = resource.title
    resource.file.delete(save=False)
    resource.delete()

    log_action(
        emp, "DELETE_RESOURCE", f"Deleted {title} from Library",
        "Resource", pk, company
    )
    messages.success(request, "File deleted successfully. Quota freed.")
    return redirect("resource_list")
