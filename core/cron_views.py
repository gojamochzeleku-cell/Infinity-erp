import os
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from datetime import timedelta
from .models import Task, Tender, LegalCase
from .services import notify

@csrf_exempt
def daily_reminders(request):
    # 1. Security Check
    token = request.GET.get('token')
    expected_token = os.environ.get('CRON_SECRET_TOKEN', 'infinity_cron_secret')
    
    if token != expected_token:
        return HttpResponse("Unauthorized", status=401)

    today = timezone.now().date()
    two_days = today + timedelta(days=2)
    three_days = today + timedelta(days=3)
    
    alerts_sent = 0

    # 2. Overdue & Due Today Tasks
    tasks = Task.objects.filter(due_date__lte=today, status__in=['PENDING', 'IN_PROGRESS'])
    for t in tasks:
        status = "OVERDUE" if t.due_date < today else "DUE TODAY"
        notify(
            t.assigned_to, 
            f"⚠️ Task {status}: {t.title}", 
            f"Your task '{t.title}' is {status}. Please complete it immediately.",
            task=t
        )
        alerts_sent += 1

    # 3. Court Hearings (Next 2 days)
    cases = LegalCase.objects.filter(next_hearing_date__range=[today, two_days], status='ACTIVE')
    for c in cases:
        if c.assigned_lawyer:
            notify(
                c.assigned_lawyer,
                f"⚖️ Upcoming Court Hearing: {c.title}",
                f"Hearing scheduled for {c.next_hearing_date}. Please prepare."
            )
            alerts_sent += 1

    # 4. Expiring CPOs (Next 3 days)
    expiring_tenders = Tender.objects.filter(cpo_expiry__range=[today, three_days])
    for t in expiring_tenders:
        # Notify Finance Manager and CEO
        from .chain import find_role
        finance = find_role(t.company, 'FIN')
        ceo = find_role(t.company, 'CEO')
        
        msg = f"CPO for {t.serial_number} expires on {t.cpo_expiry}."
        if finance:
            notify(finance, f"🚨 CPO Expiring: {t.serial_number}", msg)
            alerts_sent += 1
        if ceo:
            notify(ceo, f"🚨 CPO Expiring: {t.serial_number}", msg)
            alerts_sent += 1

    return JsonResponse({'status': 'success', 'alerts_sent': alerts_sent, 'date': str(today)})
