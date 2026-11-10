from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.contrib import messages

from .models import Tender, Task, CompanyMembership
from .chain import trigger_ai_tasks
from .services import log_action


@login_required
def repair_chain(request):
    user = request.user
    emp = getattr(user, "employee", None)
    company = getattr(request, "company", None)

    if not user.is_active or not emp or not emp.is_active or not company:
        return redirect("select_company")

    membership = CompanyMembership.objects.filter(
        employee=emp, company=company, is_active=True
    ).first()
    if not membership:
        return redirect("select_company")

    roles = set(membership.roles.values_list("role", flat=True))
    if not roles.intersection({"ADMIN", "CEO", "IT"}):
        return redirect("dashboard")

    if request.method != "POST":
        return redirect("it_dashboard")

    fixed_count = 0
    tenders = Tender.objects.filter(
        company=company,
        purchased_bid_document__isnull=False,
    ).exclude(status__in=["WON", "LOST", "COMPLETED", "DRAFT"])

    for tender in tenders:
        has_t09 = Task.objects.filter(
            company=company,
            payload__tender_id=tender.id,
            code="T09",
        ).exists()

        if not has_t09:
            Task.objects.filter(
                company=company,
                payload__tender_id=tender.id,
                code__in=["T09A", "T09B", "T09C", "T09D"],
            ).delete()

            t08 = Task.objects.filter(
                company=company,
                payload__tender_id=tender.id,
                code="T08",
            ).first()

            if t08 and t08.status != "COMPLETED":
                t08.status = "COMPLETED"
                t08.save(update_fields=["status"])

            trigger_ai_tasks(tender)
            fixed_count += 1

    log_action(
        emp,
        "REPAIR_CHAIN",
        f"Repaired {fixed_count} tenders missing T09 tasks.",
        "System",
        "",
        company,
    )

    if fixed_count:
        messages.success(
            request,
            f"Successfully repaired {fixed_count} tender(s) and generated the missing AI tasks!",
        )
    else:
        messages.info(
            request,
            "System check complete. No missing T09 tasks found for active tenders.",
        )

    return redirect("it_dashboard")
