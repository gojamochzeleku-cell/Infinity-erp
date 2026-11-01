from django.core.management.base import BaseCommand
from core.models import Notification, Task, Tender
from core.chain import trigger_ai_tasks

class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        Notification.objects.all().delete()
        self.stdout.write("Cleared all notifications.")

        for t in Tender.objects.all():
            if t.status not in ['REGISTERED', 'DRAFT', 'GO']:
                stale_tasks = Task.objects.filter(payload__tender_id=t.id, code='T02').exclude(status='COMPLETED')
                for st in stale_tasks:
                    st.status = 'CANCELLED'
                    st.save()
                    self.stdout.write(f"Cancelled stale T02 for {t.serial_number}")

        latest_tender = Tender.objects.order_by('-updated_at').first()
        if latest_tender:
            self.stdout.write(f"Latest tender: {latest_tender.bid_title}")
            has_ai = Task.objects.filter(payload__tender_id=latest_tender.id, code__startswith='T09').exists()
            if not has_ai:
                self.stdout.write("Generating missing AI tasks (T09A - T09D)...")
                trigger_ai_tasks(latest_tender)
            else:
                self.stdout.write("AI tasks already exist.")
