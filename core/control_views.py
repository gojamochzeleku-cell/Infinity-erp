from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from dateutil import parser as date_parser

from .models import (
    Tender, Task, Correspondence, LegalCase, Employee,
    CompanyMembership,
)
from .alerts import check_critical_alerts


def _control_context(request):
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


def _control_employees(company):
    return Employee.objects.filter(
        is_active=True,
        company_memberships__company=company,
        company_memberships__is_active=True,
    ).distinct().order_by("user__first_name", "user__last_name")


@login_required
def tv_dashboard(request):
    emp, company, roles = _control_context(request)
    if not company:
        return redirect("select_company")

    if roles.intersection({"ADMIN", "CEO", "GM"}):
        check_critical_alerts(company, emp)

    today = date.today()
    warning_date = today + timedelta(days=7)

    context = {
        "active_tenders": Tender.objects.filter(company=company).exclude(
            status__in=["WON", "LOST", "COMPLETED"]
        ).count(),
        "overdue_tasks": Task.objects.filter(
            company=company,
            status="PENDING",
            created_at__date__lt=today - timedelta(days=2),
        ).count(),
        "expiring_cpos": Tender.objects.filter(
            company=company, cpo_expiry__range=[today, warning_date]
        ),
        "expiring_bonds": Tender.objects.filter(
            company=company,
            performance_bond_expiry__range=[today, warning_date],
        ),
        "upcoming_cases": LegalCase.objects.filter(
            company=company,
            next_hearing_date__range=[today, warning_date],
        ),
        "overdue_letters": Correspondence.objects.filter(
            company=company,
            direction="OUTGOING",
            reply_deadline__lt=today,
        ),
        "bottlenecks": (
            Task.objects.filter(company=company, status="PENDING")
            .values("assigned_to__role")
            .annotate(count=__import__("django.db.models", fromlist=["Count"]).Count("id"))
            .order_by("-count")[:5]
        ),
    }
    return render(request, "tv_dashboard.html", context)


@login_required
def legal_list(request):
    emp, company, roles = _control_context(request)
    if not company:
        return redirect("select_company")
    if not roles.intersection({"LEGAL", "ADMIN", "CEO", "GM"}):
        return redirect("dashboard")

    cases = LegalCase.objects.filter(company=company)
    employees = _control_employees(company)
    tenders = Tender.objects.filter(company=company)

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        hearing = request.POST.get("next_hearing_date", "").strip()
        lawyer_id = request.POST.get("assigned_lawyer", "").strip()

        if not title or not description:
            return redirect("legal_list")

        lawyer = None
        if lawyer_id:
            lawyer = employees.filter(pk=lawyer_id).first()
            if not lawyer:
                return redirect("legal_list")

        hearing_date = None
        if hearing:
            try:
                hearing_date = date_parser.parse(hearing).date()
            except (ValueError, TypeError, OverflowError):
                return redirect("legal_list")

        LegalCase.objects.create(
            company=company,
            title=title,
            description=description,
            next_hearing_date=hearing_date,
            assigned_lawyer=lawyer,
        )
        return redirect("legal_list")

    return render(request, "legal_list.html", {
        "cases": cases,
        "employees": employees,
        "tenders": tenders,
        "today": timezone.localdate(),
    })


@login_required
def assign_bid_tracker(request, pk):
    emp, company, roles = _control_context(request)
    if not company:
        return redirect("select_company")
    if not roles.intersection({"ADMIN", "CEO", "GM", "VGM", "BIDM"}):
        return redirect("dashboard")

    tender = get_object_or_404(Tender, pk=pk, company=company)
    employees = _control_employees(company)

    if request.method == "POST":
        tracker_id = request.POST.get("bid_tracker", "").strip()
        tracker = employees.filter(pk=tracker_id).first() if tracker_id else None

        if not tracker:
            return redirect("tender_detail", pk=tender.id)

        tender.bid_tracker = tracker
        tender.save(update_fields=["bid_tracker"])
        return redirect("tender_detail", pk=tender.id)

    return render(request, "assign_tracker.html", {
        "tender": tender,
        "employees": employees,
    })
