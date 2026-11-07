from django.core.management.base import BaseCommand
from core.models import Tender, Task, Employee
from core.chain import trigger_ai_tasks

class Command(BaseCommand):
    help = 'Checks and forces the generation of T09 tasks for the latest tender.'

    def handle(self, *args, **kwargs):
        latest_tender = Tender.objects.order_by('-updated_at').first()
        
        if not latest_tender:
            self.stdout.write(self.style.ERROR("No tenders found in the database."))
            return

        self.stdout.write(f"--- Examining Tender: {latest_tender.serial_number} ---")
        self.stdout.write(f"Company: {latest_tender.company.name}")
        self.stdout.write(f"Status: {latest_tender.status}")
        self.stdout.write(f"Bid Document Uploaded: {bool(latest_tender.purchased_bid_document)}")
        
        self.stdout.write("\n--- Current Tasks for this Tender ---")
        tasks = Task.objects.filter(payload__tender_id=latest_tender.id).order_by('id')
        for t in tasks:
            self.stdout.write(f"[{t.code}] {t.get_status_display()} -> Assigned to: {t.assigned_to.user.username if t.assigned_to else 'UNASSIGNED'}")

        self.stdout.write("\n--- Checking Roles ---")
        roles = ['BIDM', 'FIN', 'HR', 'BIDOP']
        for r in roles:
            emps = Employee.objects.filter(role=r, is_active=True)
            names = [e.user.username for e in emps]
            self.stdout.write(f"{r}: {names}")

        # Check if T09A exists
        has_t09 = Task.objects.filter(payload__tender_id=latest_tender.id, code='T09A').exists()
        
        if not has_t09:
            self.stdout.write(self.style.WARNING("\nT09A is missing! Forcing generation of the 4 AI tasks..."))
            
            # Mark T08 as complete just in case it got stuck
            t08 = Task.objects.filter(payload__tender_id=latest_tender.id, code='T08').first()
            if t08 and t08.status != 'COMPLETED':
                t08.status = 'COMPLETED'
                t08.save()
                self.stdout.write("Marked stuck T08 as COMPLETED.")
                
            # Trigger the tasks
            trigger_ai_tasks(latest_tender)
            
            self.stdout.write(self.style.SUCCESS("Done! Re-checking tasks:"))
            for t in Task.objects.filter(payload__tender_id=latest_tender.id).order_by('id'):
                self.stdout.write(f"[{t.code}] {t.get_status_display()} -> Assigned to: {t.assigned_to.user.username if t.assigned_to else 'UNASSIGNED'}")
        else:
            self.stdout.write(self.style.SUCCESS("\nT09A exists in the database! If you can't see it, check if you are logged in with the correct user and company."))

