from django.core.management.base import BaseCommand
from core.models import Tender, Task, Employee

class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        self.stdout.write("=== CURRENT TENDERS ===")
        for t in Tender.objects.all():
            self.stdout.write(f"{t.serial_number} | {t.bid_title} | Company: {t.company.short_name} | Status: {t.status}")

        self.stdout.write("\n=== CURRENT OPEN TASKS ===")
        for task in Task.objects.exclude(status='COMPLETED'):
            self.stdout.write(f"Task {task.code} | To: {task.assigned_to.user.username} ({task.assigned_to.company.short_name}) | TenderID: {task.payload.get('tender_id')} | Status: {task.status}")

        self.stdout.write("\n=== BID MANAGERS ===")
        for emp in Employee.objects.filter(role='BIDM'):
            self.stdout.write(f"User: {emp.user.username} | Company: {emp.company.short_name}")
