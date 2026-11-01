from datetime import date, timedelta
from django.db.models import Q
from .models import Tender, Correspondence, Task, LegalCase, Employee
from .services import notify

def check_critical_alerts(company, trigger_employee):
    today = date.today()
    warning_date = today + timedelta(days=7)
    
    # 1. Expiring Bonds/CPO
    expiring_tenders = Tender.objects.filter(company=company).filter(
        Q(cpo_expiry__lte=warning_date) | 
        Q(performance_bond_expiry__lte=warning_date) | 
        Q(advance_payment_bond_expiry__lte=warning_date)
    ).exclude(cpo_expiry__lt=today, performance_bond_expiry__lt=today, advance_payment_bond_expiry__lt=today)
    
    for t in expiring_tenders:
        if t.cpo_expiry and t.cpo_expiry <= warning_date:
            notify(trigger_employee, f"CRITICAL: CPO Expiring for {t.serial_number}", f"CPO expires on {t.cpo_expiry}.")
        if t.performance_bond_expiry and t.performance_bond_expiry <= warning_date:
            notify(trigger_employee, f"CRITICAL: Performance Bond Expiring for {t.serial_number}", f"Expires on {t.performance_bond_expiry}.")
            
    # 2. Overdue Outgoing Letters
    overdue_letters = Correspondence.objects.filter(
        company=company, direction='OUTGOING', reply_deadline__lt=today
    )
    for l in overdue_letters:
        notify(trigger_employee, f"OVERDUE REPLY: {l.subject}", f"No reply received from {l.sender_recipient} by {l.reply_deadline}.")

    # 3. Overdue Tasks Bottleneck
    overdue_tasks = Task.objects.filter(company=company, status='PENDING', created_at__date__lt=today - timedelta(days=3))
    if overdue_tasks.count() > 0:
        notify(trigger_employee, f"BOTTLENECK ALERT: {overdue_tasks.count()} Overdue Tasks", "Tasks have been pending for more than 3 days.")

    # 4. Upcoming Court Hearings
    upcoming_cases = LegalCase.objects.filter(company=company, next_hearing_date__lte=warning_date, next_hearing_date__gte=today)
    for c in upcoming_cases:
        notify(trigger_employee, f"COURT HEARING: {c.title}", f"Hearing scheduled for {c.next_hearing_date}.")
