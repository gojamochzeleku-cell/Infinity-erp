import json
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import render, redirect

from .models import (
    CashFlowEntry, Tender, Task, AuditLog,
    Employee, CompanyMembership,
)
from .gemini import ask_infinity_brain


def _management_context(request):
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
def management_dashboard(request):
    emp, company, roles = _management_context(request)
    if not company:
        return redirect("select_company")
    if not roles.intersection({"ADMIN", "CEO", "GM", "VGM"}):
        return redirect("dashboard")

    tenders = Tender.objects.filter(company=company)
    financials = tenders.aggregate(
        total_cost=Sum("total_cost"),
        total_duty=Sum("duty_tax_amount"),
        total_payments=Sum("payment_amount"),
    )

    stats = {
        "total": tenders.count(),
        "won": tenders.filter(status="WON").count(),
        "lost": tenders.filter(
            status__in=["LOST", "NO_GO", "CEO_REJECTED"]
        ).count(),
        "active": tenders.exclude(
            status__in=[
                "WON", "LOST", "NO_GO", "CEO_REJECTED",
                "COMPLETED", "DRAFT",
            ]
        ).count(),
        "completed": tenders.filter(status="COMPLETED").count(),
    }

    bottlenecks = (
        Task.objects.filter(company=company)
        .exclude(status__in=["COMPLETED", "CANCELLED", "REJECTED"])
        .values("assigned_to__role")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    lost_tenders = (
        tenders.filter(status="LOST")
        .exclude(loss_reason="")
        .order_by("-updated_at")[:5]
    )
    recent_activity = AuditLog.objects.filter(
        company=company
    ).order_by("-created_at")[:15]

    six_months_ago = date.today() - timedelta(days=180)
    monthly_flows = (
        CashFlowEntry.objects.filter(
            company=company, date__gte=six_months_ago
        )
        .annotate(month=TruncMonth("date"))
        .values("month", "flow_type")
        .annotate(total=Sum("amount"))
        .order_by("month")
    )

    temp_data = {}
    for flow in monthly_flows:
        month_str = flow["month"].strftime("%b %Y")
        temp_data.setdefault(month_str, {"IN": 0, "OUT": 0})
        temp_data[month_str][flow["flow_type"]] += float(flow["total"] or 0)

    flow_labels, flow_in, flow_out = [], [], []
    for month, values in temp_data.items():
        flow_labels.append(month)
        flow_in.append(values["IN"])
        flow_out.append(values["OUT"])

    expense_data = (
        CashFlowEntry.objects.filter(company=company, flow_type="OUT")
        .values("category")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    expense_labels, expense_values = [], []
    for item in expense_data:
        expense_labels.append(
            dict(CashFlowEntry.CATEGORY_CHOICES).get(
                item["category"], item["category"]
            )
        )
        expense_values.append(float(item["total"] or 0))

    return render(request, "mgmt_dashboard.html", {
        "stats": stats,
        "financials": financials,
        "bottlenecks": bottlenecks,
        "recent_activity": recent_activity,
        "lost_tenders": lost_tenders,
        "tender_data": [stats["won"], stats["lost"], stats["active"]],
        "flow_labels": flow_labels,
        "flow_in": flow_in,
        "flow_out": flow_out,
        "expense_labels": expense_labels,
        "expense_values": expense_values,
    })


@login_required
def ai_assistant_api(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    emp, company, roles = _management_context(request)
    if not company:
        return JsonResponse({"error": "Select an active company."}, status=403)
    if not roles.intersection({"ADMIN", "CEO", "GM", "VGM"}):
        return JsonResponse({"error": "Unauthorized."}, status=403)

    try:
        data = json.loads(request.body or "{}")
        question = data.get("question", "")
        if not isinstance(question, str):
            return JsonResponse({"error": "Invalid question."}, status=400)
        question = question.strip()
        if not question or len(question) > 5000:
            return JsonResponse({"error": "Question is empty or too long."}, status=400)

        from .models import Shipment

        inflows = CashFlowEntry.objects.filter(
            company=company, flow_type="IN"
        ).aggregate(Sum("amount"))["amount__sum"] or 0
        outflows = CashFlowEntry.objects.filter(
            company=company, flow_type="OUT"
        ).aggregate(Sum("amount"))["amount__sum"] or 0

        active_tenders = Tender.objects.filter(company=company).exclude(
            status__in=["WON", "LOST", "COMPLETED", "DRAFT"]
        )
        tender_list = [
            f"- {t.serial_number}: {t.bid_title} (Status: {t.get_status_display()})"
            for t in active_tenders[:10]
        ]

        context_str = f"""
Company: {company.name}
Total Inflows: {inflows} ETB
Total Outflows: {outflows} ETB
Current Cash Balance: {inflows - outflows} ETB
Total Won Tenders: {Tender.objects.filter(company=company, status='WON').count()}
Total Lost Tenders: {Tender.objects.filter(company=company, status='LOST').count()}
Currently Active Tenders: {active_tenders.count()}
Active Tender Details (Max 10):
{chr(10).join(tender_list) if tender_list else "None"}
Pending Tasks: {Task.objects.filter(company=company, status='PENDING').count()}
Active Shipments in Transit: {Shipment.objects.filter(company=company).exclude(current_status='WAREHOUSE').count()}
"""
        answer = ask_infinity_brain(question, context_str)
        return JsonResponse({"answer": answer})
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid request data."}, status=400)
    except Exception:
        return JsonResponse({"error": "The AI service is temporarily unavailable."}, status=500)
