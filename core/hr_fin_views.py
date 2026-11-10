from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone

from .models import (
    Employee, LeaveRequest, ExpenseRequest, Task, Company,
    EmployeeDocument, Document, PayrollRecord,
    CompanyMembership, MembershipRole, MEMBERSHIP_ROLE_CHOICES,
)
from .chain import create_task, find_role
from .services import notify, log_action


def _hr_context(request):
    emp = getattr(request.user, "employee", None)
    company = getattr(request, "company", None)
    membership = getattr(request, "membership", None)

    if (
        not emp or not company or not membership
        or not membership.is_active
        or membership.company_id != company.id
        or membership.employee_id != emp.id
    ):
        return None, None, set()

    roles = set(membership.roles.values_list("role", flat=True))
    return emp, company, roles


def _has_any(roles, *required):
    return bool(roles.intersection(required))


@login_required
def request_leave(request):
    emp, company, roles = _hr_context(request)
    if not emp:
        return redirect("dashboard")

    if request.method == "POST":
        from dateutil import parser as date_parser
        try:
            start = date_parser.parse(request.POST.get("start_date", "")).date()
            end = date_parser.parse(request.POST.get("end_date", "")).date()
        except (ValueError, TypeError, OverflowError):
            messages.error(request, "Please enter valid leave dates.")
            return redirect("dashboard")

        if end < start:
            messages.error(request, "The leave end date cannot be before its start date.")
            return redirect("dashboard")

        reason = request.POST.get("reason", "").strip()
        ltype = request.POST.get("leave_type", "Annual").strip()

        leave = LeaveRequest.objects.create(
            employee=emp, company=company, leave_type=ltype,
            start_date=start, end_date=end, reason=reason
        )

        hr_manager = find_role(company, "HR")
        if hr_manager:
            task = Task.objects.create(
                code="HR_LEAVE",
                title=f"Approve Leave: {emp.full_name}",
                description=f"{ltype} Leave from {start} to {end}. Reason: {reason}",
                kind="ROUTINE",
                company=company,
                assigned_to=hr_manager,
                payload={"leave_id": leave.id},
            )
            notify(hr_manager, "New Leave Request", task.title, task)

        messages.success(request, "Your leave request has been submitted.")
        return redirect("dashboard")

    return render(request, "request_leave.html")


@login_required
def request_expense(request):
    emp, company, roles = _hr_context(request)
    if not emp:
        return redirect("dashboard")

    if request.method == "POST":
        try:
            amount = Decimal(request.POST.get("amount", "0"))
            months = int(request.POST.get("repayment_months", "1"))
        except (InvalidOperation, ValueError, TypeError):
            messages.error(request, "Please enter a valid amount and repayment period.")
            return redirect("dashboard")

        if amount <= 0 or months < 1:
            messages.error(request, "Amount and repayment period must be positive.")
            return redirect("dashboard")

        reason = request.POST.get("reason", "").strip()
        req_type = request.POST.get("request_type", "EXPENSE")
        if req_type not in ("EXPENSE", "LOAN"):
            req_type = "EXPENSE"

        exp = ExpenseRequest.objects.create(
            employee=emp, company=company, request_type=req_type,
            amount=amount, reason=reason, repayment_months=months
        )

        fin_manager = find_role(company, "FIN")
        if fin_manager:
            label = "Loan" if req_type == "LOAN" else "Expense"
            desc = f"Amount: {amount} ETB. Reason: {reason}"
            if req_type == "LOAN":
                desc += f" (Repayment: {months} months)"
            task = Task.objects.create(
                code="FIN_EXPENSE",
                title=f"Approve {label}: {emp.full_name}",
                description=desc,
                kind="ROUTINE",
                company=company,
                assigned_to=fin_manager,
                payload={"expense_id": exp.id},
            )
            notify(fin_manager, f"New {label} Request", task.title, task)

        messages.success(request, "Your request has been submitted.")
        return redirect("dashboard")

    return render(request, "request_expense.html")


@login_required
def register_employee(request):
    emp, company, roles = _hr_context(request)
    if not emp or not _has_any(roles, "ADMIN", "CEO", "GM", "HR"):
        return redirect("dashboard")

    role_choices = MEMBERSHIP_ROLE_CHOICES

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        password = request.POST.get("password", "")
        role = request.POST.get("role", "STAFF")
        phone = request.POST.get("phone", "").strip()
        telegram = request.POST.get("telegram", "").strip()
        whatsapp = request.POST.get("whatsapp", "").strip()

        valid_roles = {code for code, label in role_choices}
        if not username or not password or role not in valid_roles:
            return render(request, "register_employee.html", {
                "error": "Please provide a username, password and valid company role.",
                "roles": role_choices,
            })

        if User.objects.filter(username=username).exists():
            return render(request, "register_employee.html", {
                "error": "That username already exists. Use a new username.",
                "roles": role_choices,
            })

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    is_staff=False,
                    is_superuser=False,
                )
                new_emp = Employee.objects.create(
                    user=user,
                    company=company,
                    role="STAFF",
                    phone=phone,
                    telegram_chat_id=telegram,
                    whatsapp_number=whatsapp,
                    is_group_level=False,
                    is_active=True,
                )
                membership = CompanyMembership.objects.create(
                    employee=new_emp,
                    company=company,
                    is_active=True,
                )
                MembershipRole.objects.create(membership=membership, role=role)

            log_action(
                emp, "REGISTER_EMPLOYEE",
                f"Created company membership for {username} with role {role}",
                "Employee", new_emp.id, company
            )
            messages.success(request, f"Employee {username} was created for {company.short_name}.")
            return redirect("staff_directory")

        except Exception:
            messages.error(request, "Employee registration failed. No partial account was retained.")
            return render(request, "register_employee.html", {"roles": role_choices})

    return render(request, "register_employee.html", {"roles": role_choices})


@login_required
def staff_directory(request):
    emp, company, roles = _hr_context(request)
    if not emp:
        return redirect("dashboard")

    query = request.GET.get("q", "").strip()
    memberships = (
        CompanyMembership.objects
        .filter(company=company, is_active=True, employee__is_active=True)
        .select_related("employee__user", "company")
        .prefetch_related("roles")
        .order_by("employee__user__first_name", "employee__user__username")
    )

    if query:
        memberships = memberships.filter(
            Q(employee__user__first_name__icontains=query)
            | Q(employee__user__last_name__icontains=query)
            | Q(employee__user__username__icontains=query)
            | Q(roles__role__icontains=query)
        ).distinct()

    return render(request, "staff_directory.html", {
        "memberships": memberships,
        "query": query,
        "membership_roles": roles,
    })


@login_required
def employee_profile(request, pk):
    emp, company, roles = _hr_context(request)
    if not emp:
        return redirect("dashboard")

    membership = get_object_or_404(
        CompanyMembership.objects.select_related("employee__user", "company")
        .prefetch_related("roles"),
        employee_id=pk,
        company=company,
        is_active=True,
        employee__is_active=True,
    )
    target_emp = membership.employee

    can_manage = _has_any(roles, "ADMIN", "CEO", "GM", "HR")
    if not can_manage and target_emp.pk != emp.pk:
        return redirect("dashboard")

    if request.method == "POST":
        if "update_telegram" in request.POST:
            if target_emp.pk != emp.pk:
                messages.error(
                    request,
                    "Only the account owner can update the shared Telegram identity."
                )
                return redirect("employee_profile", pk=target_emp.pk)

            target_emp.telegram_id = request.POST.get("telegram_id", "").strip() or None
            target_emp.save(update_fields=["telegram_id"])
            messages.success(request, "Your Telegram ID was updated.")
            return redirect("employee_profile", pk=target_emp.pk)

        if "doc_type" in request.POST:
            if not can_manage:
                return redirect("dashboard")

            doc_type = request.POST.get("doc_type", "")
            title = request.POST.get("title", "").strip()
            file_obj = request.FILES.get("file")
            allowed_types = {code for code, label in EmployeeDocument.DOC_TYPES}

            if file_obj and title and doc_type in allowed_types:
                with transaction.atomic():
                    reg_doc = Document.objects.create(
                        company=company,
                        title=f"HR: {target_emp.full_name} - {title}",
                        file=file_obj,
                        uploaded_by=emp,
                        category="HR",
                    )
                    EmployeeDocument.objects.create(
                        employee=target_emp,
                        doc_type=doc_type,
                        title=title,
                        file=file_obj,
                        registry_doc=reg_doc,
                    )
                messages.success(request, "Employee document uploaded.")
            else:
                messages.error(request, "Please provide a valid document type, title and file.")
            return redirect("employee_profile", pk=target_emp.pk)

        # PerformanceReview has no company field yet. Do not create or expose
        # unscoped reviews until that model is made company-aware.
        if "rating" in request.POST:
            messages.warning(
                request,
                "Performance reviews are temporarily unavailable until they can be stored separately by company."
            )
            return redirect("employee_profile", pk=target_emp.pk)

    documents = (
        EmployeeDocument.objects
        .filter(employee=target_emp, registry_doc__company=company)
        .select_related("registry_doc")
        .order_by("-uploaded_at")
    )

    return render(request, "employee_profile.html", {
        "target_emp": target_emp,
        "company": company,
        "membership": membership,
        "employee_documents": documents,
        "can_manage": can_manage,
    })


@login_required
def generate_payroll(request):
    emp, company, roles = _hr_context(request)
    if not emp or not _has_any(roles, "HR", "ADMIN"):
        return redirect("dashboard")

    if request.method == "POST":
        month = request.POST.get("month", "")
        try:
            year = int(request.POST.get("year", ""))
        except (ValueError, TypeError):
            year = 0

        valid_months = {
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        }
        if month not in valid_months or year < 2000 or year > 2200:
            messages.error(request, "Please select a valid payroll month and year.")
            return redirect("generate_payroll")

        if PayrollRecord.objects.filter(company=company, month=month, year=year).exists():
            messages.warning(request, "Payroll for this company and month already exists.")
            return redirect("payroll_history")

        memberships = CompanyMembership.objects.filter(
            company=company,
            is_active=True,
            employee__is_active=True,
        )
        total = memberships.aggregate(total=Sum("base_salary"))["total"] or Decimal("0.00")

        if total <= 0:
            messages.error(
                request,
                "Payroll was not generated: this company has no positive membership salary records. Enter verified salaries first."
            )
            return redirect("generate_payroll")

        finance_manager = find_role(company, "FIN")
        if not finance_manager:
            messages.error(
                request,
                "Payroll was not generated because this company has no designated Finance recipient."
            )
            return redirect("generate_payroll")

        with transaction.atomic():
            payroll = PayrollRecord.objects.create(
                company=company,
                month=month,
                year=year,
                total_amount=total,
            )
            task = create_task(
                company,
                "FIN_PAYROLL",
                "FIN",
                f"Execute Payroll: {month} {year}",
                f"Total Payroll Amount: {total} ETB. Please process salaries.",
                {"payroll_id": payroll.id, "amount": str(total)},
                reverse("dashboard"),
            )

            if task is None:
                payroll.delete()
                messages.error(request, "Payroll task could not be assigned. No payroll record was retained.")
                return redirect("generate_payroll")

        messages.success(request, f"Payroll created for {company.short_name}: {total} ETB.")
        return redirect("payroll_history")

    return render(request, "generate_payroll.html")


@login_required
def payroll_history(request):
    emp, company, roles = _hr_context(request)
    if not emp or not _has_any(roles, "HR", "ADMIN", "FIN"):
        return redirect("dashboard")

    payrolls = PayrollRecord.objects.filter(company=company).order_by("-created_at")
    return render(request, "payroll_history.html", {
        "payrolls": payrolls,
        "membership_roles": roles,
    })
