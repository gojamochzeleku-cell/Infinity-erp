import csv
from datetime import date, timedelta

from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db.models import Q

from .models import (
    Tender, CashFlowEntry, Task, LegalCase, LeaveRequest,
    AuditLog, CompanyMembership,
)


def _advanced_context(request):
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
    return emp, company, set(membership.roles.values_list("role", flat=True))


@login_required
def export_tenders_csv(request):
    emp, company, roles = _advanced_context(request)
    if not company:
        return redirect("select_company")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="tenders_{company.short_name}_{date.today()}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow([
        "Serial Number", "Bid Title", "Purchaser", "Status",
        "Final Price (ETB)", "Submission Date", "Loss Reason",
    ])
    tenders = Tender.objects.filter(company=company).order_by("-created_at")
    for tender in tenders:
        writer.writerow([
            tender.serial_number, tender.bid_title, tender.purchaser_name,
            tender.get_status_display(), tender.final_price,
            tender.bid_submission_date, tender.loss_reason,
        ])
    return response


@login_required
def export_cashflow_csv(request):
    emp, company, roles = _advanced_context(request)
    if not company:
        return redirect("select_company")
    if not roles.intersection({"FIN", "CEO", "ADMIN"}):
        return redirect("dashboard")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="cashflow_{company.short_name}_{date.today()}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow([
        "Date", "Type", "Category", "Amount (ETB)",
        "Description", "Linked Tender",
    ])
    entries = CashFlowEntry.objects.filter(
        company=company
    ).select_related("related_tender").order_by("-date")
    for entry in entries:
        writer.writerow([
            entry.date, entry.flow_type, entry.get_category_display(),
            entry.amount, entry.description,
            entry.related_tender.serial_number if entry.related_tender else "",
        ])
    return response


@login_required
def corporate_calendar(request):
    emp, company, roles = _advanced_context(request)
    if not company:
        return redirect("select_company")

    today = date.today()
    end_date = today + timedelta(days=30)
    events = []

    for tender in Tender.objects.filter(company=company).exclude(
        status__in=["WON", "LOST", "COMPLETED"]
    ):
        if tender.bid_submission_date and today <= tender.bid_submission_date <= end_date:
            events.append({
                "date": tender.bid_submission_date,
                "title": f"BID SUBMISSION: {tender.serial_number}",
                "type": "TENDER",
            })
        if tender.bid_opening_date and today <= tender.bid_opening_date <= end_date:
            events.append({
                "date": tender.bid_opening_date,
                "title": f"BID OPENING: {tender.serial_number}",
                "type": "TENDER",
            })

    for case in LegalCase.objects.filter(
        company=company,
        status="ACTIVE",
        next_hearing_date__range=[today, end_date],
    ):
        events.append({
            "date": case.next_hearing_date,
            "title": f"COURT HEARING: {case.title}",
            "type": "LEGAL",
        })

    for task in Task.objects.filter(
        company=company, due_date__range=[today, end_date]
    ).exclude(status="COMPLETED"):
        events.append({
            "date": task.due_date,
            "title": f"TASK DUE: {task.title}",
            "type": "TASK",
        })

    for leave in LeaveRequest.objects.filter(
        company=company,
        status="APPROVED",
        start_date__range=[today, end_date],
    ).select_related("employee"):
        events.append({
            "date": leave.start_date,
            "title": f"LEAVE STARTS: {leave.employee.full_name}",
            "type": "HR",
        })

    events.sort(key=lambda item: item["date"])
    grouped_events = {}
    for event in events:
        grouped_events.setdefault(event["date"], []).append(event)

    return render(request, "corporate_calendar.html", {
        "grouped_events": grouped_events,
        "today": today,
    })


@login_required
def audit_trail(request):
    emp, company, roles = _advanced_context(request)
    if not company:
        return redirect("select_company")
    if not roles.intersection({"ADMIN", "CEO", "GM", "IT"}):
        return redirect("dashboard")

    query = request.GET.get("q", "").strip()[:150]
    logs = AuditLog.objects.filter(company=company).select_related(
        "actor__user"
    ).order_by("-created_at")

    if query:
        logs = logs.filter(
            Q(action__icontains=query)
            | Q(detail__icontains=query)
            | Q(actor__user__username__icontains=query)
        )

    return render(request, "audit_trail.html", {
        "logs": logs[:200],
        "query": query,
    })
