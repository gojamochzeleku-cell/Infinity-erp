from django.core.management.base import BaseCommand
from core.models import Task, Tender, Notification
from core.chain import create_task, trigger_ai_tasks
from django.urls import reverse

class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        # Clear all notifications and open tasks
        Notification.objects.all().delete()
        Task.objects.exclude(status='COMPLETED').delete()
        self.stdout.write("Cleared all pending tasks and notifications.")

        # Recreate the correct task based on the tender's current status
        for t in Tender.objects.all():
            url = reverse('tender_detail', args=[t.id])
            if t.status == 'REGISTERED':
                create_task(t.company, 'T02', 'BIDM', f'Feasibility review: {t.bid_title}', 'Review feasibility and press GO or NO-GO.', {'tender_id': t.id}, url)
            elif t.status == 'GO':
                create_task(t.company, 'T03', 'CEO', f'CEO Approval: {t.bid_title}', 'Bid Manager marked GO.', {'tender_id': t.id}, url)
            elif t.status == 'CEO_APPROVED':
                create_task(t.company, 'T04', 'CASE', f'Cost Request: {t.bid_title}', 'CEO Approved.', {'tender_id': t.id}, url)
            elif t.status == 'WON':
                trigger_ai_tasks(t)
                
        self.stdout.write("Regenerated tasks perfectly based on Tender statuses.")
