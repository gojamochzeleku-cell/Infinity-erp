from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404

from .models import Tender, Document, Employee, Task, CompanyMembership
from .services import notify


def _global_context(request):
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


def _company_employees(company):
    return Employee.objects.filter(
        is_active=True,
        company_memberships__company=company,
        company_memberships__is_active=True,
    ).distinct().order_by("user__first_name", "user__last_name", "user__username")


@login_required
def global_search(request):
    emp, company, roles = _global_context(request)
    if not company:
        return redirect("select_company")

    query = request.GET.get("q", "").strip()[:150]
    if not query:
        return render(request, "search_results.html", {"query": query})

    tenders = Tender.objects.filter(company=company).filter(
        Q(bid_title__icontains=query)
        | Q(serial_number__icontains=query)
        | Q(purchaser_name__icontains=query)
    )[:10]

    documents = Document.objects.filter(company=company).filter(
        Q(title__icontains=query) | Q(doc_id__icontains=query)
    )[:10]

    employees = _company_employees(company).filter(
        Q(user__first_name__icontains=query)
        | Q(user__last_name__icontains=query)
        | Q(company_memberships__roles__role__icontains=query)
    ).distinct()[:10]

    return render(request, "search_results.html", {
        "query": query,
        "tenders": tenders,
        "documents": documents,
        "employees": employees,
    })


@login_required
def reassign_task(request, pk):
    emp, company, roles = _global_context(request)
    if not company:
        return redirect("select_company")
    if not roles.intersection({"ADMIN", "CEO", "GM", "VGM"}):
        return redirect("dashboard")
    if request.method not in ("GET", "POST"):
        return redirect("dashboard")

    task = get_object_or_404(Task, pk=pk, company=company)
    employees = _company_employees(company).exclude(pk=task.assigned_to_id)

    if request.method == "POST":
        new_emp_id = request.POST.get("new_employee", "").strip()
        new_emp = employees.filter(pk=new_emp_id).first()
        if not new_emp:
            return redirect("reassign_task", pk=task.id)

        old_emp = task.assigned_to
        task.assigned_to = new_emp
        task.save(update_fields=["assigned_to", "updated_at"])

        notify(
            new_emp,
            f"Task Reassigned to You: {task.title}",
            f"Reassigned by {emp.full_name}",
            task,
            "/dashboard/",
        )

        from .services import log_action
        log_action(
            emp,
            "TASK_REASSIGNED",
            f"Reassigned {task.code} from {old_emp.full_name} to {new_emp.full_name}",
            "Task",
            task.id,
            company,
        )
        return redirect("task_detail", pk=task.id)

    return render(request, "reassign_task.html", {
        "task": task,
        "employees": employees,
    })
