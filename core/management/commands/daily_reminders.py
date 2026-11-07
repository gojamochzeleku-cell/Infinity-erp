from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from core.models import Tender, Task, LegalCase
from core.services import notify

class Command(BaseCommand):
    help = 'Sends daily reminders for overdue tasks, expiring CPOs, and upcoming court cases.'

    def handle(self, *args, **kwargs):
        today = timezone.now().date()
        three_days = today + timedelta(days=3)
        seven_days = today + timedelta(days=7)
        
        alerts_sent = 0

        # 1. Overdue Tasks
        overdue_tasks = Task.objects.filter(due_date__lt=today, status__in=['PENDING', 'IN_PROGRESS'])
        for task in overdue_tasks:
            notify(task.assigned_to, f'⚠️ OVERDUE Task: {task.title}', f'This task was due on {task.due_date}. Please complete it immediately.', task)
            alerts_sent += 1

        # 2. Expiring CPOs (Next 3 days)
        expiring_cpos = Tender.objects.filter(cpo_expiry__range=[today, three_days]).exclude(status='COMPLETED')
        for t in expiring_cpos:
            # Find Finance and CEO
            from core.chain import find_role
            fin = find_role(t.company, 'FIN')
            ceo = find_role(t.company, 'CEO')
            msg = f"CPO for {t.serial_number} expires on {t.cpo_expiry}."
            if fin: notify(fin, f'🚨 CPO Expiring: {t.serial_number}', msg); alerts_sent += 1
            if ceo: notify(ceo, f'🚨 CPO Expiring: {t.serial_number}', msg); alerts_sent += 1

        # 3. Upcoming Court Cases (Next 7 days)
        upcoming_cases = LegalCase.objects.filter(next_hearing_date__range=[today, seven_days], status='ACTIVE')
        for case in upcoming_cases:
            if case.assigned_lawyer:
                notify(case.assigned_lawyer, f'⚖️ Upcoming Hearing: {case.title}', f'Hearing scheduled for {case.next_hearing_date}.', None)
                alerts_sent += 1

        self.stdout.write(self.style.SUCCESS(f'Daily reminders sent: {alerts_sent} alerts generated.'))
