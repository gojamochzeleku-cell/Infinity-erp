import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404

from .models import (
    ITTicket, Employee, Task, CompanyMembership, MembershipRole,
    CompanyRoleAssignment, MEMBERSHIP_ROLE_CHOICES,
)
from .chain import create_task


def _it_context(request):
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


def _target_membership(pk, company):
    return get_object_or_404(
        CompanyMembership.objects.select_related("employee__user", "company")
        .prefetch_related("roles"),
        employee_id=pk,
        company=company,
    )


def _deactivation_block_reason(membership, company):
    """Protect the company's designated ADMIN and its last active ADMIN."""
    holds_admin = membership.roles.filter(role="ADMIN").exists()
    if not holds_admin:
        return ""

    is_designated_admin = CompanyRoleAssignment.objects.filter(
        company=company,
        role="ADMIN",
        membership=membership,
    ).exists()

    if is_designated_admin:
        return (
            "This membership is the designated company ADMIN. "
            "Assign the ADMIN designation to another active ADMIN first."
        )

    other_active_admins = (
        CompanyMembership.objects
        .filter(
            company=company,
            is_active=True,
            employee__is_active=True,
            roles__role="ADMIN",
        )
        .exclude(pk=membership.pk)
        .distinct()
        .count()
    )

    if other_active_admins == 0:
        return "This is the company's last active ADMIN membership."

    return ""


@login_required
def it_dashboard(request):
    emp, company, roles = _it_context(request)
    if not emp or not roles.intersection({"IT", "ADMIN"}):
        return redirect("dashboard")

    tickets = ITTicket.objects.filter(company=company).order_by("-created_at")

    gemini_key = os.environ.get("GEMINI_API_KEY", "") or getattr(settings, "GEMINI_API_KEY", "")
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    health = {
        "gemini": "OK" if gemini_key and gemini_key != "PASTE_YOUR_GEMINI_API_KEY_HERE" else "MISSING",
        "telegram": "OK" if telegram_token else "MISSING",
        "db": "OK",
    }

    return render(request, "it_dashboard.html", {"tickets": tickets, "health": health})


@login_required
def it_user_management(request):
    emp, company, roles = _it_context(request)
    if not emp or not roles.intersection({"IT", "ADMIN"}):
        return redirect("dashboard")

    if request.method == "POST":
        action = request.POST.get("action", "create_user")

        if action in {"set_workflow_role", "set_bid_manager"}:
            workflow_role_codes = {
                "BIDM", "CEO", "GM", "CASE", "FIN",
                "OFFICE", "HR", "BIDOP", "LEGAL", "PROC", "LOG",
            }
            role = (
                "BIDM" if action == "set_bid_manager"
                else request.POST.get("workflow_role", "").strip()
            )
            membership_id = (
                request.POST.get("bid_manager_membership", "").strip()
                if action == "set_bid_manager"
                else request.POST.get("workflow_membership", "").strip()
            )

            if role not in workflow_role_codes:
                messages.error(request, "Invalid workflow role.")
                return redirect("it_user_management")

            if not membership_id:
                CompanyRoleAssignment.objects.filter(
                    company=company, role=role
                ).delete()
                messages.success(request, f"Designated {role} recipient cleared.")
                return redirect("it_user_management")

            membership = (
                CompanyMembership.objects
                .filter(
                    pk=membership_id,
                    company=company,
                    is_active=True,
                    employee__is_active=True,
                    roles__role=role,
                )
                .select_related("employee__user")
                .first()
            )

            if membership is None:
                messages.error(
                    request,
                    "Select an active employee holding this role in this company."
                )
                return redirect("it_user_management")

            try:
                with transaction.atomic():
                    CompanyRoleAssignment.objects.update_or_create(
                        company=company,
                        role=role,
                        defaults={"membership": membership},
                    )
                messages.success(
                    request,
                    f"{membership.employee.full_name} is now the designated "
                    f"{role} recipient for {company.short_name}."
                )
            except Exception:
                messages.error(request, f"Could not designate the {role} recipient.")

            return redirect("it_user_management")

        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        password = request.POST.get("password", "")
        role = request.POST.get("role", "STAFF")
        phone = request.POST.get("phone", "").strip()
        telegram_id = request.POST.get("telegram_id", "").strip()

        valid_roles = {code for code, label in MEMBERSHIP_ROLE_CHOICES}
        if not username or not password or role not in valid_roles:
            messages.error(request, "Enter a username, password and valid company role.")
            return redirect("it_user_management")

        if User.objects.filter(username=username).exists():
            messages.error(request, "That username already exists. No existing account was changed.")
            return redirect("it_user_management")

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    email=email,
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
                    telegram_id=telegram_id or None,
                    is_group_level=False,
                    is_active=True,
                )
                membership = CompanyMembership.objects.create(
                    employee=new_emp,
                    company=company,
                    is_active=True,
                )
                MembershipRole.objects.create(membership=membership, role=role)

            messages.success(request, f"User {username} created for {company.short_name}.")
        except Exception:
            messages.error(request, "User creation failed. No partial account was retained.")

        return redirect("it_user_management")

    memberships = (
        CompanyMembership.objects
        .filter(company=company)
        .select_related("employee__user")
        .prefetch_related("roles")
        .order_by("employee__user__username")
    )

    workflow_role_codes = [
        "BIDM", "CEO", "GM", "CASE", "FIN",
        "OFFICE", "HR", "BIDOP", "LEGAL", "PROC", "LOG",
    ]
    role_labels = dict(MEMBERSHIP_ROLE_CHOICES)
    workflow_roles = []

    for role_code in workflow_role_codes:
        candidates = list(
            memberships.filter(
                is_active=True,
                employee__is_active=True,
                roles__role=role_code,
            ).distinct()
        )
        assignment = (
            CompanyRoleAssignment.objects
            .filter(company=company, role=role_code)
            .select_related("membership__employee__user")
            .first()
        )
        workflow_roles.append({
            "code": role_code,
            "label": role_labels.get(role_code, role_code),
            "candidates": candidates,
            "selected_membership_id": (
                assignment.membership_id if assignment else None
            ),
        })


    return render(request, "it_users.html", {
        "memberships": memberships,
        "roles": MEMBERSHIP_ROLE_CHOICES,
        "company": company,
        "workflow_roles": workflow_roles,
    })


@login_required
def it_delete_user(request, pk):
    emp, company, roles = _it_context(request)
    if not emp or not roles.intersection({"IT", "ADMIN"}):
        return redirect("dashboard")
    if request.method != "POST":
        return redirect("it_user_management")

    membership = _target_membership(pk, company)
    target_emp = membership.employee

    if target_emp.pk == emp.pk:
        messages.error(request, "You cannot remove your own active company membership.")
        return redirect("it_user_management")

    block_reason = _deactivation_block_reason(membership, company)
    if block_reason:
        messages.error(request, block_reason)
        return redirect("it_user_management")

    with transaction.atomic():
        CompanyRoleAssignment.objects.filter(
            company=company,
            membership=membership,
        ).delete()
        membership.is_active = False
        membership.save(update_fields=["is_active"])

    messages.success(
        request,
        f"{target_emp.full_name} was removed from {company.short_name}. Their login and other company memberships were preserved."
    )
    return redirect("it_user_management")


@login_required
def it_reset_password(request, pk):
    # Passwords belong to the shared User login, not one company membership.
    # This endpoint is intentionally disabled to prevent one company from
    # changing another company's shared login credentials.
    messages.warning(
        request,
        "Company IT cannot reset a shared login password. Use the account's approved password recovery process."
    )
    return redirect("it_user_management")


@login_required
def it_toggle_user(request, pk):
    emp, company, roles = _it_context(request)
    if not emp or not roles.intersection({"IT", "ADMIN"}):
        return redirect("dashboard")
    if request.method != "POST":
        return redirect("it_user_management")

    membership = _target_membership(pk, company)
    target_emp = membership.employee

    if target_emp.pk == emp.pk:
        messages.error(request, "You cannot deactivate your own company membership.")
        return redirect("it_user_management")

    # Apply ADMIN safeguards only when deactivating.
    # Reactivation must remain possible for an inactive membership.
    if membership.is_active:
        block_reason = _deactivation_block_reason(membership, company)
        if block_reason:
            messages.error(request, block_reason)
            return redirect("it_user_management")

    with transaction.atomic():
        membership.is_active = not membership.is_active
        membership.save(update_fields=["is_active"])

        if not membership.is_active:
            CompanyRoleAssignment.objects.filter(
                company=company,
                membership=membership,
            ).delete()

    status = "activated" if membership.is_active else "deactivated"
    messages.success(
        request,
        f"{target_emp.full_name}'s membership in {company.short_name} was {status}. Other company memberships and the login account were not changed."
    )
    return redirect("it_user_management")


@login_required
def it_submit_ticket(request):
    emp, company, roles = _it_context(request)
    if not emp:
        return redirect("dashboard")

    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        desc = request.POST.get("description", "").strip()

        if not subject or not desc:
            messages.error(request, "Please enter a subject and description.")
            return redirect("dashboard")

        ticket = ITTicket.objects.create(
            company=company,
            employee=emp,
            subject=subject,
            description=desc,
        )
        create_task(
            company,
            "IT_TICKET",
            "IT",
            f"IT Support: {subject}",
            f"From: {emp.full_name}\n\n{desc}",
            {"ticket_id": ticket.id},
            "",
        )
        messages.success(request, "IT ticket submitted.")
        return redirect("dashboard")

    return render(request, "it_ticket_form.html")


@login_required
def it_resolve_ticket(request, pk):
    emp, company, roles = _it_context(request)
    if not emp or not roles.intersection({"IT", "ADMIN"}):
        return redirect("dashboard")
    if request.method != "POST":
        return redirect("it_dashboard")

    ticket = get_object_or_404(ITTicket, pk=pk, company=company)
    ticket.status = "RESOLVED"
    ticket.save(update_fields=["status"])
    messages.success(request, "Ticket marked as resolved.")
    return redirect("it_dashboard")


@login_required
def it_edit_user(request, pk):
    emp, company, roles = _it_context(request)
    if not emp or not roles.intersection({"IT", "ADMIN"}):
        return redirect("dashboard")

    membership = _target_membership(pk, company)
    target_emp = membership.employee

    if request.method == "POST":
        selected_role = request.POST.get("role", "STAFF")
        valid_roles = {code for code, label in MEMBERSHIP_ROLE_CHOICES}
        if selected_role not in valid_roles:
            messages.error(request, "Invalid company role.")
            return redirect("it_edit_user", pk=pk)

        # Phone and Telegram ID belong to the shared Employee identity.
        # Company IT must not edit those global identity fields.
        old_roles = set(membership.roles.values_list("role", flat=True))
        new_roles = {selected_role}
        removed_roles = old_roles - new_roles

        # Never remove the designated or last active company ADMIN.
        if "ADMIN" in removed_roles:
            block_reason = _deactivation_block_reason(membership, company)
            if block_reason:
                messages.error(request, block_reason)
                return redirect("it_edit_user", pk=pk)

        with transaction.atomic():
            if removed_roles:
                CompanyRoleAssignment.objects.filter(
                    company=company,
                    membership=membership,
                    role__in=removed_roles,
                ).delete()

            membership.roles.exclude(role=selected_role).delete()
            MembershipRole.objects.get_or_create(
                membership=membership,
                role=selected_role,
            )

        messages.success(
            request,
            f"{target_emp.full_name}'s {company.short_name} membership was updated. Shared account name and email were not changed."
        )
        return redirect("it_user_management")

    current_roles = set(membership.roles.values_list("role", flat=True))
    return render(request, "it_edit_user.html", {
        "target_emp": target_emp,
        "membership": membership,
        "roles": MEMBERSHIP_ROLE_CHOICES,
        "current_roles": current_roles,
        "company": company,
    })
