from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.mail import EmailMessage
from django.shortcuts import render, redirect
from django.utils import timezone
from dateutil import parser as date_parser

from .models import (
    Correspondence, Employee, CompanyMembership, Document,
)


def _mail_context(request):
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
    ).distinct().order_by("user__first_name", "user__last_name")


@login_required
def mail_list(request):
    emp, company, roles = _mail_context(request)
    if not company:
        return redirect("select_company")

    letters = Correspondence.objects.filter(company=company)
    direction_filter = request.GET.get("type", "")
    if direction_filter in {"INCOMING", "OUTGOING"}:
        letters = letters.filter(direction=direction_filter)
    else:
        direction_filter = ""

    return render(request, "mail_list.html", {
        "letters": letters,
        "direction_filter": direction_filter,
    })


@login_required
def mail_create(request, direction):
    emp, company, roles = _mail_context(request)
    if not company:
        return redirect("select_company")
    if not roles.intersection({"OFFICE", "ADMIN"}):
        return redirect("dashboard")
    if direction not in {"INCOMING", "OUTGOING"}:
        return redirect("mail_list")

    employees = _company_employees(company)

    if request.method == "POST":
        try:
            ref = request.POST.get("reference_number", "").strip()
            raw_date = request.POST.get("letter_date", "").strip()
            letter_date = date_parser.parse(raw_date).date()
            sender_recipient = request.POST.get("sender_recipient", "").strip()
            subject = request.POST.get("subject", "").strip()
            assigned_id = request.POST.get("assigned_to", "").strip()
            pdf_file = request.FILES.get("file")

            if not sender_recipient or not subject:
                raise ValueError("Sender/recipient and subject are required.")

            assigned_emp = None
            if assigned_id:
                assigned_emp = employees.filter(pk=assigned_id).first()
                if not assigned_emp:
                    raise ValueError("The selected employee is not an active member of this company.")

            letter = Correspondence.objects.create(
                company=company,
                direction=direction,
                reference_number=ref,
                letter_date=letter_date,
                sender_recipient=sender_recipient,
                subject=subject,
                file=pdf_file,
                uploaded_by=emp,
                assigned_to=assigned_emp,
            )

            if pdf_file:
                Document.objects.create(
                    company=company,
                    title=f"{direction} Letter: {subject}",
                    file=pdf_file,
                    uploaded_by=emp,
                    category="MAIL",
                )

            messages.success(request, "Correspondence registered successfully.")
            return redirect("mail_list")
        except (ValueError, TypeError, OverflowError):
            messages.error(request, "Please check the date and required fields, then try again.")

    return render(request, "mail_form.html", {
        "direction": direction,
        "employees": employees,
    })


@login_required
def compose_email(request):
    emp, company, roles = _mail_context(request)
    if not company:
        return redirect("select_company")
    if not roles.intersection({"OFFICE", "ADMIN"}):
        return redirect("dashboard")

    if request.method == "POST":
        to_email = request.POST.get("to_email", "").strip()
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "")
        file_obj = request.FILES.get("attachment")

        if not to_email or not subject:
            messages.error(request, "Recipient and subject are required.")
            return render(request, "compose_email.html")

        try:
            from django.conf import settings
            email_user, email_pass = settings.get_company_email(company)

            from django.core.mail import get_connection
            connection = get_connection(
                username=email_user,
                password=email_pass,
                fail_silently=False,
            )

            email = EmailMessage(
                subject, body, email_user, [to_email], connection=connection
            )
            if file_obj:
                email.attach(
                    file_obj.name, file_obj.read(), file_obj.content_type
                )
            email.send()

            if file_obj:
                file_obj.seek(0)

            letter = Correspondence.objects.create(
                company=company,
                direction="OUTGOING",
                sender_recipient=to_email,
                subject=subject,
                file=file_obj,
                uploaded_by=emp,
                letter_date=timezone.localdate(),
            )

            if file_obj:
                file_obj.seek(0)
                Document.objects.create(
                    company=company,
                    title=f"Email Attachment: {subject}",
                    file=file_obj,
                    uploaded_by=emp,
                    category="MAIL",
                )

            from .services import log_action
            log_action(
                emp, "SEND_EMAIL",
                f"Sent email to {to_email} - Subject: {subject}",
                "Correspondence", letter.id, company,
            )
            messages.success(request, "Email sent successfully.")
            return redirect("mail_list")
        except Exception:
            messages.error(
                request,
                "Email could not be sent. Check the company email configuration and try again.",
            )

    return render(request, "compose_email.html")


@login_required
def fetch_emails_action(request):
    if request.method != "POST":
        return redirect("mail_list")

    emp, company, roles = _mail_context(request)
    if not company:
        return redirect("select_company")
    if not roles.intersection({"OFFICE", "ADMIN"}):
        return redirect("dashboard")

    from .email_fetcher import fetch_new_emails
    result = fetch_new_emails(company, emp)

    if "error" in result:
        messages.error(request, result["error"])
    elif result.get("count", 0) == 0:
        messages.info(request, result.get("message", "No new emails found."))
    else:
        messages.success(
            request,
            f"Successfully fetched {result['count']} new emails.",
        )

    return redirect("mail_list")
